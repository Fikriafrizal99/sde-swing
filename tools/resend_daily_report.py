#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.delivery import deliver
from modules.job_runner.enhanced_runtime_bridge import (
    market_outlook_payloads as enhanced_market_outlook_payloads,
    post_market_payloads as enhanced_post_market_payloads,
)
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

SUPPORTED_JOBS = {"market_outlook", "post_market"}
_SUCCESS_MANIFEST_STATUSES = {
    "SUCCESS",
    "SUCCESS_WITH_WARNING",
    "SUCCESS_WITH_EXISTING_SNAPSHOT",
}


class ResendArtifactNotFound(RuntimeError):
    pass


def _manifest_trade_date(payload: dict[str, Any]) -> str:
    return str(
        payload.get("Technical_Date")
        or payload.get("trade_date")
        or payload.get("Trade_Date")
        or payload.get("snapshot_trade_date")
        or ""
    )


def find_existing_run_manifest(manifest_dir: Path, trade_date: str) -> tuple[Path | None, dict[str, Any]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    if manifest_dir.exists():
        for path in manifest_dir.glob("SWING_RUN_MANIFEST_*.json"):
            payload = read_json(path)
            if not isinstance(payload, dict) or not payload:
                continue
            if _manifest_trade_date(payload) != trade_date:
                continue
            status = str(payload.get("Pipeline_Status", "")).upper()
            if status and status not in _SUCCESS_MANIFEST_STATUSES:
                continue
            matches.append((path, payload))
    if not matches:
        return None, {}
    return max(matches, key=lambda item: item[0].stat().st_mtime)


def _market_outlook_payloads(ctx):
    trade_date = ctx.trade_date.isoformat()
    global_path = resolve("data/output/global_market") / trade_date / "global_market_snapshot.json"
    regime_path = ctx.previews_root.parent / "market_regime" / trade_date / "market_outlook_regime.json"

    global_snapshot = read_json(global_path)
    market_status = read_json(regime_path)
    missing: list[str] = []
    if not global_snapshot:
        missing.append(str(global_path))
    if not market_status:
        missing.append(str(regime_path))
    if missing:
        raise ResendArtifactNotFound("MARKET_OUTLOOK_ARTIFACT_NOT_FOUND:" + " | ".join(missing))

    payloads = enhanced_market_outlook_payloads(ctx, global_snapshot, market_status)
    return payloads, {
        "source_global_market": str(global_path),
        "source_market_regime": str(regime_path),
    }


def _post_market_payloads(ctx):
    trade_date = ctx.trade_date.isoformat()
    manifest_dir = ctx.path("manifest_dir", "data/output/manifests")
    manifest_path, manifest = find_existing_run_manifest(manifest_dir, trade_date)
    if manifest_path is None:
        raise ResendArtifactNotFound(f"POST_MARKET_ARTIFACT_NOT_FOUND:{trade_date}")

    # Preserve the original manifest lineage; the resend run itself is delivery-only.
    manifest = dict(manifest)
    manifest.setdefault("Manifest_Path", str(manifest_path))
    payloads = enhanced_post_market_payloads(ctx, manifest)
    return payloads, {
        "source_run_id": manifest.get("Run_ID", ""),
        "source_manifest": str(manifest_path),
        "source_snapshot": str(manifest.get("Snapshot_Manifest") or manifest.get("snapshot_manifest") or ""),
    }


def build_existing_payloads(ctx):
    if ctx.job == "market_outlook":
        return _market_outlook_payloads(ctx)
    if ctx.job == "post_market":
        return _post_market_payloads(ctx)
    raise ValueError(f"UNSUPPORTED_RESEND_JOB:{ctx.job}")


def _delivery_result(delivery: list[dict[str, Any]]) -> tuple[str, str, int]:
    if any(str(item.get("status", "")).upper() == "FAILED" for item in delivery):
        return "FAILED", "FAILED", EXIT_DELIVERY_FAILED
    if any(str(item.get("status", "")).upper().startswith("SENT") for item in delivery):
        return "SUCCESS", "SENT", EXIT_SUCCESS
    return "SUCCESS", "SKIPPED", EXIT_SUCCESS


def _message_ids(delivery: list[dict[str, Any]]) -> list[Any]:
    result: list[Any] = []
    for item in delivery:
        if item.get("telegram_message_ids"):
            result.extend(item.get("telegram_message_ids", []))
        elif item.get("telegram_message_id"):
            result.append(item.get("telegram_message_id"))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kirim ulang Market Outlook/Post Market dari artifact existing tanpa menjalankan engine"
    )
    parser.add_argument("--job", required=True, choices=sorted(SUPPORTED_JOBS))
    parser.add_argument("--trade-date", required=True, help="Tanggal trading YYYY-MM-DD")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ctx = load_context(
        job=args.job,
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
    # Resend is delivery-only: the canonical Post Market builder still
    # validates the requested-date pulse, but it must not trigger a new data
    # refresh or rerun the engine.
    setattr(ctx, "delivery_only", True)

    write_status(ctx, "RUNNING", "RESEND_EXISTING", EXIT_SUCCESS, {
        "engine_status": "NOT_RUN",
        "report_status": "RUNNING",
        "delivery_status": "NOT_RUN",
        "warnings": ["DELIVERY_ONLY_RESEND; engine dan dependency graph tidak dijalankan."],
    })

    try:
        with FileLock(ctx):
            payloads, source_details = build_existing_payloads(ctx)
            if not payloads:
                write_status(ctx, "FAILED", "RESEND_REPORT_BUILD", EXIT_FAILED, {
                    "engine_status": "NOT_RUN",
                    "report_status": "FAILED",
                    "delivery_status": "NOT_RUN",
                    "errors": [f"{ctx.job.upper()}_PAYLOAD_EMPTY"],
                    **source_details,
                })
                return EXIT_FAILED

            preview_paths = write_payloads(ctx, payloads)
            delivery = deliver(ctx, payloads)
            overall, telegram_status, code = _delivery_result(delivery)
            stage = f"{ctx.job.upper()}_RESEND"
            write_status(ctx, overall, stage, code, {
                "engine_status": "NOT_RUN",
                "report_status": "SUCCESS",
                "delivery_status": telegram_status,
                "telegram_status": telegram_status,
                "telegram_message_ids": _message_ids(delivery),
                "telegram_part_count": sum(int(item.get("part_count") or 0) for item in delivery),
                "preview_paths": [str(path) for path in preview_paths],
                "delivery": delivery,
                "warnings": ["RESEND_EXISTING_ARTIFACT; engine tidak dijalankan ulang."],
                **source_details,
            })
            return code
    except ResendArtifactNotFound as exc:
        write_status(ctx, "FAILED", "RESEND_ARTIFACT_DISCOVERY", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "errors": [str(exc)],
            "warnings": ["Resend tidak menjalankan ulang engine."],
        })
        return EXIT_FAILED
    except ReportSourceValidationError as exc:
        write_status(ctx, "FAILED", "RESEND_SOURCE_VALIDATION", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "FAILED",
            "delivery_status": "NOT_RUN",
            "errors": list(exc.errors),
            "warnings": ["Artifact ditemukan tetapi source report tidak lolos validasi."],
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
            "errors": [f"{type(exc).__name__}: {exc}"],
        })
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
