#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from modules.job_runner.delivery import deliver
from modules.job_runner.enhanced_runtime_bridge import final_watchlist_payloads as enhanced_final_watchlist_payloads
from modules.job_runner.reports import write_payloads
from modules.job_runner.report_validation import ReportSourceValidationError
from modules.job_runner.runtime import (
    EXIT_DELIVERY_FAILED,
    EXIT_FAILED,
    EXIT_SUCCESS,
    FileLock,
    JobAlreadyRunning,
    ResourceLocked,
    load_context,
    read_json,
    resolve,
    write_status,
)


def _manifest_trade_date(payload: dict) -> str:
    return str(
        payload.get("Technical_Date")
        or payload.get("trade_date")
        or payload.get("Trade_Date")
        or payload.get("snapshot_trade_date")
        or ""
    )


def find_existing_run_manifest(manifest_dir: Path, trade_date: str) -> tuple[Path | None, dict]:
    matches: list[tuple[Path, dict]] = []
    if manifest_dir.exists():
        for path in manifest_dir.glob("SWING_RUN_MANIFEST_*.json"):
            payload = read_json(path)
            if not isinstance(payload, dict) or not payload:
                continue
            if _manifest_trade_date(payload) != trade_date:
                continue
            status = str(payload.get("Pipeline_Status", "")).upper()
            if status and status not in {
                "SUCCESS",
                "SUCCESS_WITH_WARNING",
                "SUCCESS_WITH_EXISTING_SNAPSHOT",
            }:
                continue
            matches.append((path, payload))
    if not matches:
        return None, {}
    return max(matches, key=lambda item: item[0].stat().st_mtime)


def _csv_last(payloads):
    normal = []
    csv_payloads = []
    for payload in payloads:
        raw = getattr(payload, "attachment_path", None)
        if raw and Path(raw).suffix.lower() == ".csv":
            csv_payloads.append(payload)
        else:
            normal.append(payload)
    return normal + csv_payloads


def _delivery_status(delivery: list[dict]) -> tuple[str, int]:
    if any(item.get("status") == "FAILED" for item in delivery):
        return "FAILED", EXIT_DELIVERY_FAILED
    return "SUCCESS", EXIT_SUCCESS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kirim ulang FINAL WATCHLIST dari artifact engine yang sudah ada tanpa menjalankan engine/dependency graph"
    )
    parser.add_argument("--trade-date", required=True, help="Tanggal analisis YYYY-MM-DD")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ctx = load_context(
        job="final_watchlist",
        config_path=args.config,
        scheduler_config_path=args.scheduler_config,
        trade_date=args.trade_date,
        dry_run=False,
        preview_existing=False,
        no_telegram=False,
        force=True,
        debug=False,
        interactive_broker=False,
    )

    manifest_dir = ctx.path("manifest_dir", "data/output/manifests")
    manifest_path, manifest = find_existing_run_manifest(manifest_dir, ctx.trade_date.isoformat())
    if manifest_path is None:
        write_status(ctx, "FAILED", "RESEND_ARTIFACT_DISCOVERY", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "errors": [f"FINAL_WATCHLIST_ARTIFACT_NOT_FOUND:{ctx.trade_date.isoformat()}"],
            "warnings": ["Resend tidak menjalankan ulang engine."],
        })
        return EXIT_FAILED

    write_status(ctx, "RUNNING", "RESEND_EXISTING", EXIT_SUCCESS, {
        "engine_status": "NOT_RUN",
        "report_status": "RUNNING",
        "delivery_status": "NOT_RUN",
        "source_run_id": manifest.get("Run_ID", ""),
        "source_manifest": str(manifest_path),
    })

    try:
        with FileLock(ctx):
            payloads = enhanced_final_watchlist_payloads(ctx, manifest)
            payloads = _csv_last(payloads)
            if not payloads:
                write_status(ctx, "FAILED", "RESEND_REPORT_BUILD", EXIT_FAILED, {
                    "engine_status": "NOT_RUN",
                    "report_status": "FAILED",
                    "delivery_status": "NOT_RUN",
                    "source_run_id": manifest.get("Run_ID", ""),
                    "source_manifest": str(manifest_path),
                    "errors": ["FINAL_WATCHLIST_PAYLOAD_EMPTY"],
                })
                return EXIT_FAILED

            preview_paths = write_payloads(ctx, payloads)
            delivery = deliver(ctx, payloads)
            overall, code = _delivery_status(delivery)
            telegram_status = (
                "FAILED"
                if any(item.get("status") == "FAILED" for item in delivery)
                else "SENT"
                if any(item.get("status") == "SENT" for item in delivery)
                else "SKIPPED"
            )
            message_ids = []
            for item in delivery:
                if item.get("telegram_message_ids"):
                    message_ids.extend(item.get("telegram_message_ids", []))
                elif item.get("telegram_message_id"):
                    message_ids.append(item.get("telegram_message_id"))
            write_status(ctx, overall, "FINAL_WATCHLIST_RESEND", code, {
                "engine_status": "NOT_RUN",
                "report_status": "SUCCESS",
                "delivery_status": telegram_status,
                "telegram_status": telegram_status,
                "telegram_message_ids": message_ids,
                "telegram_part_count": sum(int(item.get("part_count") or 0) for item in delivery),
                "source_run_id": manifest.get("Run_ID", ""),
                "source_manifest": str(manifest_path),
                "preview_paths": [str(path) for path in preview_paths],
                "delivery": delivery,
                "warnings": ["RESEND_EXISTING_ARTIFACT; engine tidak dijalankan ulang."],
            })
            return code
    except ReportSourceValidationError as exc:
        write_status(ctx, "FAILED", "RESEND_SOURCE_VALIDATION", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "FAILED",
            "delivery_status": "NOT_RUN",
            "source_run_id": manifest.get("Run_ID", ""),
            "source_manifest": str(manifest_path),
            "errors": list(exc.errors),
            "warnings": ["Artifact ditemukan tetapi source final watchlist tidak lolos validasi."],
        })
        return EXIT_FAILED
    except (JobAlreadyRunning, ResourceLocked) as exc:
        write_status(ctx, "SKIPPED", "RESEND_LOCK", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "errors": [str(exc)],
        })
        return EXIT_FAILED
    except Exception as exc:
        write_status(ctx, "FAILED", "RESEND_EXCEPTION", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "FAILED",
            "delivery_status": "NOT_RUN",
            "source_run_id": manifest.get("Run_ID", ""),
            "source_manifest": str(manifest_path),
            "errors": [f"{type(exc).__name__}: {exc}"],
        })
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
