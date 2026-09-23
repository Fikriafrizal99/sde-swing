#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.ai_interpretation import GeminiInterpreter
from modules.job_runner import enhanced_runtime_bridge as bridge
from modules.job_runner import final_watchlist_snapshot as snapshot_runtime
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder
from modules.job_runner.existing_delivery import ExactDeliveryError
from modules.job_runner.final_watchlist_snapshot import (
    MAX_DETAIL_CARDS,
    create_snapshot,
    load_snapshot,
    replay_snapshot,
)
from modules.job_runner.runtime import (
    EXIT_FAILED,
    EXIT_SUCCESS,
    FileLock,
    JobAlreadyRunning,
    ResourceLocked,
    load_context,
    resolve,
    write_status,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview/copy the last completed Final Watchlist presentation without rerunning the trading engine."
    )
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--preview-only", action="store_true")
    return parser.parse_args()


def _preview_builder(ctx):
    """Build presentation only; never invoke AI during Preview/Cek."""
    cfg = ctx.scheduler_config.get("enhanced_reporting", {})
    builder = EnhancedDailyReportBuilder(
        output_root=resolve(cfg.get("output_root", "data/output")),
        interpreter=GeminiInterpreter(api_key="", enabled=False, cache_enabled=False),
        max_watchlist_messages=MAX_DETAIL_CARDS,
    )
    builder.historical_dir = ctx.path("historical_dir", "data/output/historical/by_symbol")
    # Preview charts are run-scoped so opening [2] never replaces the normal
    # Final Watchlist chart files produced by the live job.
    builder.chart_output_root = (
        ctx.previews_root
        / ctx.trade_date.isoformat()
        / f"{ctx.run_id}_rendered_charts"
    )
    return builder


def _canonical_csv_path(ctx) -> Path:
    cfg = ctx.scheduler_config.get("enhanced_reporting", {})
    output_root = resolve(cfg.get("output_root", "data/output"))
    return output_root / "final_watchlist" / f"sde-final-watchlist-{ctx.trade_date.isoformat()}.csv"


def _clear_canonical_detail_previews(ctx) -> list[Path]:
    """Remove only mutable Final Watchlist detail aliases before a fresh preview render."""
    folder = ctx.previews_root / ctx.trade_date.isoformat()
    if not folder.exists():
        return []
    removed: list[Path] = []
    for path in folder.glob("final_watchlist_detail_*.txt"):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(path)
    return removed


def _restore_canonical_csv(path: Path, existed: bool, backup: bytes | None) -> None:
    if existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(backup or b"")
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _decision_manifest_hint(ctx) -> dict[str, str]:
    """Bind Preview to the exact existing Decision Engine run, never preview run-id."""
    decisions_path = ctx.path("decision_output_dir", "data/output/decision") / "FINAL_DECISION_V3.csv"
    if not decisions_path.exists() or not decisions_path.is_file() or decisions_path.stat().st_size <= 0:
        raise ExactDeliveryError(f"FINAL_WATCHLIST_DECISION_SOURCE_NOT_FOUND:{decisions_path}")

    with decisions_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ExactDeliveryError("FINAL_WATCHLIST_DECISION_SOURCE_EMPTY")

    run_ids = {
        str(row.get("Run_ID") or "").strip()
        for row in rows
        if str(row.get("Run_ID") or "").strip()
    }
    if len(run_ids) != 1:
        rendered = ",".join(sorted(run_ids)) if run_ids else "MISSING"
        raise ExactDeliveryError(f"FINAL_WATCHLIST_DECISION_RUN_ID_AMBIGUOUS:{rendered}")
    run_id = next(iter(run_ids))

    date_aliases = ("Trade_Date", "Technical_Date", "Analysis_Date", "Data_Date", "Date")
    observed_dates: set[str] = set()
    for row in rows:
        for key in date_aliases:
            value = str(row.get(key) or "").strip()[:10]
            if len(value) == 10 and value[4:5] == "-" and value[7:8] == "-":
                observed_dates.add(value)
    if observed_dates and ctx.trade_date.isoformat() not in observed_dates:
        raise ExactDeliveryError(
            "FINAL_WATCHLIST_DECISION_DATE_MISMATCH:"
            + ",".join(sorted(observed_dates))
            + f":{ctx.trade_date.isoformat()}"
        )
    return {"Run_ID": run_id}


def _delivery_summary(delivery: list[dict]) -> tuple[str, str, int]:
    statuses = [str(item.get("status") or "").upper() for item in delivery]
    if statuses and all(status in {"SENT", "SNAPSHOT_ALREADY_SENT"} for status in statuses):
        return "SUCCESS", "SENT", EXIT_SUCCESS
    if any(status == "DELIVERY_STATE_UNCERTAIN" for status in statuses):
        return "FAILED", "DELIVERY_STATE_UNCERTAIN", 50
    return "FAILED", "FAILED", 50


def _message_ids(delivery: list[dict]) -> list[int]:
    result: list[int] = []
    for item in delivery:
        for value in item.get("telegram_message_ids", []) or []:
            try:
                message_id = int(value)
            except (TypeError, ValueError):
                continue
            if message_id > 0:
                result.append(message_id)
    return result


def main() -> int:
    args = parse_args()
    ctx = load_context(
        job="final_watchlist",
        config_path=args.config,
        scheduler_config_path=args.scheduler_config,
        trade_date=args.trade_date,
        dry_run=False,
        preview_existing=bool(args.preview_only),
        no_telegram=bool(args.preview_only),
        force=not bool(args.preview_only),
        debug=False,
        interactive_broker=False,
    )
    setattr(ctx, "delivery_only", True)
    mode = "PREVIEW_CURRENT_PRESENTATION" if args.preview_only else "RESEND_APPROVED_PRESENTATION"

    write_status(ctx, "RUNNING", mode, EXIT_SUCCESS, {
        "engine_status": "NOT_RUN",
        "report_status": "NOT_RUN",
        "delivery_status": "NOT_RUN",
        "warnings": [
            "FINAL_WATCHLIST_PRESENTATION_ONLY; engine/scoring/decision/broker calculations are not rerun."
        ],
    })

    try:
        with FileLock(ctx):
            if args.preview_only:
                original_builder = bridge._builder
                original_snapshot_payload_builder = snapshot_runtime.final_watchlist_payloads
                canonical_csv = _canonical_csv_path(ctx)
                csv_existed = canonical_csv.exists() and canonical_csv.is_file()
                csv_backup = canonical_csv.read_bytes() if csv_existed else None
                source_manifest = _decision_manifest_hint(ctx)
                _clear_canonical_detail_previews(ctx)
                bridge._builder = _preview_builder
                snapshot_runtime.final_watchlist_payloads = (
                    lambda preview_ctx: original_snapshot_payload_builder(preview_ctx, source_manifest)
                )
                try:
                    snapshot = create_snapshot(ctx)
                finally:
                    snapshot_runtime.final_watchlist_payloads = original_snapshot_payload_builder
                    bridge._builder = original_builder
                    _restore_canonical_csv(canonical_csv, csv_existed, csv_backup)

                preview_paths = list(snapshot.get("preview_paths") or [])
                report_types = list(snapshot.get("report_types") or [])
                detail_count = int(snapshot.get("detail_count") or 0)
                write_status(ctx, "SUCCESS", "FINAL_WATCHLIST_PREVIEW_CURRENT", EXIT_SUCCESS, {
                    "engine_status": "NOT_RUN",
                    "report_status": "PRESENTATION_SNAPSHOT_READY",
                    "delivery_status": "SKIPPED_PREVIEW_ONLY",
                    "telegram_status": "SKIPPED",
                    "preview_paths": preview_paths,
                    "preview_selection": snapshot.get("selection_path"),
                    "snapshot_run_id": snapshot.get("snapshot_run_id"),
                    "snapshot_signature": snapshot.get("snapshot_signature"),
                    "source_decision_run_id": source_manifest.get("Run_ID"),
                    "canonical_report_types": report_types,
                    "snapshot_message_count": len(report_types),
                    "detail_card_count": detail_count,
                    "detail_card_limit": MAX_DETAIL_CARDS,
                    "exact_resend_readiness": "READY",
                    "canonical_csv_restored": True,
                    "warnings": [
                        "PREVIEW_CURRENT_PRESENTATION; formatter compact terbaru diterapkan ke hasil trading yang sudah ada.",
                        "NO_ENGINE_RERUN; Final Decision, entry plan, broker facts, dan score tidak dihitung ulang.",
                        "SOURCE_RUN_LOCKED; Preview memakai Run_ID yang tertanam di FINAL_DECISION_V3.csv.",
                        "NO_AI_RERUN; Preview tidak memanggil interpreter AI.",
                        "CANONICAL_OUTPUT_PRESERVED; chart preview run-scoped dan CSV canonical dikembalikan setelah snapshot dibekukan.",
                        "PREVIEW_SEND_LOCKED; menu [3] hanya mengirim snapshot hash-locked yang dibuat oleh preview ini.",
                    ],
                })
                print(f"TRADE DATE: {ctx.trade_date.isoformat()}")
                print(f"SOURCE DECISION RUN: {source_manifest.get('Run_ID')}")
                print(f"SNAPSHOT RUN: {snapshot.get('snapshot_run_id')}")
                print("BUNDLE: 1 summary + " + str(detail_count) + " chart-card + 1 CSV")
                print(f"DETAIL LIMIT: {MAX_DETAIL_CARDS}")
                print("REPORT TYPES: " + ", ".join(report_types))
                print("RESEND READY: READY")
                print("PREVIEW FILES:")
                for path in preview_paths:
                    print(path)
                return EXIT_SUCCESS

            source = load_snapshot(ctx)
            delivery = replay_snapshot(ctx, source)
            overall, telegram_status, code = _delivery_summary(delivery)
            ids = _message_ids(delivery)
            write_status(ctx, overall, "FINAL_WATCHLIST_RESEND_APPROVED_SNAPSHOT", code, {
                "engine_status": "NOT_RUN",
                "report_status": "REUSED_APPROVED_PRESENTATION_SNAPSHOT",
                "delivery_status": telegram_status,
                "telegram_status": telegram_status,
                "telegram_message_ids": ids,
                "telegram_part_count": len(ids),
                "source_run_id": source.source_run_id,
                "source_delivery_signature": source.signature,
                "canonical_report_types": [str(item.get("report_type") or "") for item in source.entries],
                "snapshot_message_count": len(source.entries),
                "detail_card_count": sum(
                    1 for item in source.entries
                    if str(item.get("report_type") or "") == "final_watchlist_detail"
                ),
                "detail_card_limit": MAX_DETAIL_CARDS,
                "delivery": delivery,
                "warnings": [
                    "FINAL_WATCHLIST_APPROVED_SNAPSHOT_REPLAY; yang dikirim sama dengan snapshot yang terakhir dicek di menu [2].",
                    "NO_FORMATTER_ON_RESEND; menu [3] tidak merender ulang report.",
                ],
            })
            for item in delivery:
                print(
                    f"- {item.get('report_type')}: {item.get('status')}, "
                    f"parts={item.get('part_count', 0)}, via={item.get('copy_mode')}"
                )
                if item.get("error"):
                    print(f"  error: {item.get('error')}")
            return code
    except ExactDeliveryError as exc:
        write_status(ctx, "FAILED", f"{mode}_SOURCE", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "exact_resend_readiness": "BLOCKED",
            "errors": [str(exc)],
            "warnings": [
                "FINAL_WATCHLIST_SEND_ABORTED_BEFORE_SEND; tidak ada request Telegram baru dari attempt ini.",
                "Jalankan menu [2] Preview/Cek terlebih dahulu sebelum menu [3].",
            ],
        })
        print(str(exc), file=sys.stderr)
        return EXIT_FAILED
    except (JobAlreadyRunning, ResourceLocked) as exc:
        write_status(ctx, "SKIPPED", f"{mode}_LOCK", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "errors": [str(exc)],
        })
        return EXIT_FAILED
    except Exception as exc:
        write_status(ctx, "FAILED", f"{mode}_EXCEPTION", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "FAILED",
            "errors": [f"{type(exc).__name__}: {exc}"],
        })
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
