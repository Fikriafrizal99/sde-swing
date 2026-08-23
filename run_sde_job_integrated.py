#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import traceback
from pathlib import Path

from modules.job_runner.delivery import deliver
from modules.job_runner.enhanced_runtime_bridge import (
    broker_multiday_payloads,
    broker_summary_payloads,
    final_watchlist_payloads,
    lifecycle_payloads,
    market_outlook_payloads,
    post_market_payloads,
)
from modules.job_runner.final_watchlist_delivery_policy import (
    apply_final_watchlist_delivery_policy,
    prepare_final_watchlist_detail_generation,
)
from modules.job_runner.reports import write_payloads
from modules.job_runner.report_validation import ReportSourceValidationError, record_validation_error
from modules.job_runner.runtime import (
    EXIT_DELIVERY_FAILED,
    EXIT_FAILED,
    EXIT_RESOURCE_LOCKED,
    EXIT_SKIPPED,
    EXIT_SUCCESS,
    FileLock,
    JobAlreadyRunning,
    ResourceLocked,
    append_job_log,
    load_context,
    read_json,
    resolve,
    write_traceback,
    write_status,
)


ENHANCED_JOBS = {
    "market_outlook",
    "post_market",
    "broker_summary",
    "broker_multi_day",
    "final_watchlist",
    "full_manual",
}

SUPPORTED_JOBS = (
    "pre_market",
    "market_outlook",
    "post_market",
    "technical_snapshot",
    "broker_summary",
    "broker_multi_day",
    "universe_selection",
    "candidate_selection",
    "final_watchlist",
    "final_decision",
    "telegram_delivery",
    "job_status",
    "full_manual",
)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SDE Swing V1.7.1 integrated runner: deterministic engine + source validation + Gemini interpretation + Telegram UI"
    )
    parser.add_argument("--job", required=True, choices=SUPPORTED_JOBS)
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preview-existing", action="store_true")
    parser.add_argument("--interactive-broker", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--reuse-yahoo-refresh", action="store_true", help="Reuse latest valid Yahoo refresh manifest")
    return parser.parse_args()


def _engine_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable,
        "-u",
        "run_sde_job.py",
        "--job",
        args.job,
        "--config",
        args.config,
        "--scheduler-config",
        args.scheduler_config,
        "--no-telegram",
        "--engine-only",
        "--run-id",
        run_id,
        "--parent-managed-lifecycle",
    ]
    if args.trade_date:
        command.extend(["--trade-date", args.trade_date])
    if args.dry_run:
        command.append("--dry-run")
    if args.preview_existing:
        command.append("--preview-existing")
    if args.interactive_broker:
        command.append("--interactive-broker")
    if args.force:
        command.append("--force")
    if args.debug:
        command.append("--debug")
    if args.reuse_yahoo_refresh:
        command.append("--reuse-yahoo-refresh")
    return command


def _load_market_artifacts(ctx) -> tuple[dict, dict]:
    snapshot_path = resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"
    global_snapshot = read_json(snapshot_path)
    market_status = read_json(
        ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
    )
    return global_snapshot, market_status


def _load_post_manifest(ctx) -> dict:
    path = ctx.path("manifest_dir", "data/output/manifests") / f"SWING_RUN_MANIFEST_{ctx.run_id}.json"
    return read_json(path)


def _expected_nonreportable_warning(job: str, engine_status: str, errors: list[str]) -> bool:
    if job != "market_outlook" or engine_status not in {"SUCCESS_WITH_WARNING", "PARTIAL"}:
        return False
    allowed_prefixes = ("SECTOR_ROTATION_STATUS:INSUFFICIENT_DATA", "FIELD_EMPTY:sector_rotation.")
    return bool(errors) and all(any(str(error).startswith(prefix) for prefix in allowed_prefixes) for error in errors)


def _run_source_reconciliation(ctx, job: str) -> dict:
    if job not in {"post_market", "final_watchlist", "full_manual"}:
        return {}
    manifest = _load_post_manifest(ctx)
    result = manifest.get("reconciliation") if isinstance(manifest.get("reconciliation"), dict) else {}
    if result:
        append_job_log(ctx, "YAHOO_ZAPI_RECONCILIATION_REUSED", str({
            "status": result.get("status"), "run_id": result.get("run_id")
        }))
    return result


def _enhanced_payloads(ctx, job: str):
    if job == "market_outlook":
        global_snapshot, market_status = _load_market_artifacts(ctx)
        return market_outlook_payloads(ctx, global_snapshot, market_status)
    if job == "post_market":
        return post_market_payloads(ctx, _load_post_manifest(ctx))
    if job == "broker_summary":
        return broker_summary_payloads(ctx)
    if job == "broker_multi_day":
        return broker_multiday_payloads(ctx)
    if job == "final_watchlist":
        return (
            broker_summary_payloads(ctx)
            + _optional_broker_multiday_payloads(ctx)
            + final_watchlist_payloads(ctx)
            + lifecycle_payloads(ctx)
        )
    if job == "full_manual":
        global_snapshot, market_status = _load_market_artifacts(ctx)
        return (
            market_outlook_payloads(ctx, global_snapshot, market_status)
            + post_market_payloads(ctx, _load_post_manifest(ctx))
            + broker_summary_payloads(ctx)
            + _optional_broker_multiday_payloads(ctx)
            + final_watchlist_payloads(ctx)
            + lifecycle_payloads(ctx)
        )
    return []


def _optional_broker_multiday_payloads(ctx):
    """Keep final/full reports usable when history is explicitly insufficient.

    The standalone broker_multi_day report remains fail-closed and reports its
    validation error.  Final Watchlist can still be generated from valid
    one-day fusion; the skipped multi-day report is recorded in the audit log.
    """
    try:
        return broker_multiday_payloads(ctx)
    except ReportSourceValidationError as exc:
        insufficient = any(
            "DATA_QUALITY_NOT_VALID" in error and any(
                marker in error.upper() for marker in ("INSUFFICIENT_HISTORY", "PARTIAL_COVERAGE")
            )
            for error in exc.errors
        )
        missing_manifest = (
            exc.report_type == "broker_multi_day_manifest"
            and any(str(error).startswith("INPUT_FILE_NOT_FOUND:") for error in exc.errors)
        )
        if not insufficient and not missing_manifest:
            raise
        record_validation_error(ctx, exc)
        append_job_log(ctx, "BROKER_MULTI_DAY_REPORT_SKIPPED", str({
            "errors": exc.errors,
            "input_paths": exc.input_paths,
            "source_of_truth": exc.source_of_truth,
            "reason": "MISSING_MANIFEST" if missing_manifest else "INSUFFICIENT_HISTORY",
        }))
        return []


def _run_integrated(args: argparse.Namespace, ctx) -> int:
    engine = subprocess.run(_engine_command(args, ctx.run_id), cwd=Path(__file__).resolve().parent)
    engine_payload = read_json(ctx.status_root / f"engine_result_{ctx.run_id}.json")
    engine_status = str(
        engine_payload.get("legacy_status")
        or engine_payload.get("status_v1_7")
        or engine_payload.get("status")
        or ("SUCCESS" if engine.returncode == 0 else "FAILED")
    ).upper()
    if engine.returncode != 0:
        engine_details = engine_payload.get("details", {})
        if not isinstance(engine_details, dict):
            engine_details = {}
        write_status(
            ctx,
            str(engine_payload.get("status") or ("WAITING_DATA" if engine.returncode == 20 else "SKIPPED" if engine.returncode == 10 else "FAILED")),
            str(engine_payload.get("stage") or "ENGINE_EXIT"),
            int(engine.returncode),
            {
                **engine_details,
                "engine_status": engine_status,
                "report_status": "NOT_RUN_ENGINE_EXIT",
                "delivery_status": "SKIPPED_ENGINE_NOT_SUCCESSFUL",
                "telegram_status": "SKIPPED",
                "warnings": engine_payload.get("warnings", engine_details.get("warnings", [])),
                "errors": engine_payload.get("errors", engine_details.get("errors", [])),
            },
        )
        return int(engine.returncode)
    if args.job not in ENHANCED_JOBS:
        return int(engine.returncode)

    try:
        reconciliation = _run_source_reconciliation(ctx, args.job)
    except ReportSourceValidationError as exc:
        record_validation_error(ctx, exc)
        write_status(ctx, "FAILED", "SOURCE_RECONCILIATION", 1, {
            "errors": exc.errors,
            "report_type": exc.report_type,
            "input_paths": exc.input_paths,
            "source_of_truth": exc.source_of_truth,
        })
        return 1
    try:
        if args.job in {"final_watchlist", "full_manual"}:
            prepare_final_watchlist_detail_generation(ctx)
        payloads = _enhanced_payloads(ctx, args.job)
        if args.job in {"final_watchlist", "full_manual"}:
            payloads = apply_final_watchlist_delivery_policy(ctx, payloads)
        preview_paths = write_payloads(ctx, payloads)
    except ReportSourceValidationError as exc:
        record_validation_error(ctx, exc)
        if _expected_nonreportable_warning(args.job, engine_status, exc.errors):
            write_status(ctx, "SUCCESS_WITH_WARNING", "REPORT_SKIPPED_INSUFFICIENT_DATA", EXIT_SUCCESS, {
                "engine_status": engine_status,
                "report_status": "SKIPPED_INSUFFICIENT_DATA",
                "delivery_status": "SKIPPED_NO_PAYLOAD",
                "telegram_status": "SKIPPED",
                "warnings": exc.errors,
                "report_type": exc.report_type,
                "input_paths": exc.input_paths,
                "source_of_truth": exc.source_of_truth,
            })
            return EXIT_SUCCESS
        write_status(ctx, "FAILED", "REPORT_SOURCE_VALIDATION", 1, {
            "errors": exc.errors,
            "report_type": exc.report_type,
            "input_paths": exc.input_paths,
            "source_of_truth": exc.source_of_truth,
        })
        return 1
    except Exception as exc:
        error = ReportSourceValidationError(
            "report_generation",
            [f"{type(exc).__name__}:{exc}"],
            details={"job": args.job},
        )
        record_validation_error(ctx, error)
        write_status(ctx, "FAILED", "REPORT_GENERATION", 1, {
            "errors": error.errors,
            "report_type": error.report_type,
        })
        return 1
    common_status = {
        "preview_paths": [str(path) for path in preview_paths],
        "enhanced_report_count": len(payloads),
        "ai_interpretation": "GEMINI_OR_DETERMINISTIC_FALLBACK",
        "source_reconciliation": reconciliation,
        "execution_source": "YAHOO_HISTORICAL",
        "validation_source": reconciliation.get("validation_source") or "NOT_APPLICABLE",
        "engine_status": engine_status,
        "report_status": "SUCCESS" if payloads else "SKIPPED_NO_PAYLOAD",
    }
    if args.no_telegram:
        overall = "SUCCESS_WITH_WARNING" if engine_status in {"SUCCESS_WITH_WARNING", "PARTIAL"} else "SUCCESS"
        write_status(ctx, overall, "ENHANCED_REPORT_PREVIEW", EXIT_SUCCESS, {
            **common_status,
            "delivery_status": "SKIPPED_DISABLED",
            "telegram_status": "SKIPPED",
        })
        return EXIT_SUCCESS

    if not payloads:
        write_status(ctx, "SUCCESS_WITH_WARNING", "ENHANCED_REPORT_NO_PAYLOAD", EXIT_SUCCESS, {
            **common_status,
            "delivery_status": "SKIPPED_NO_PAYLOAD",
            "telegram_status": "SKIPPED",
            "warnings": ["REPORT_PAYLOAD_EMPTY_TELEGRAM_NOT_CALLED"],
        })
        return EXIT_SUCCESS

    delivery = deliver(ctx, payloads)
    failed = [item for item in delivery if item.get("status") == "FAILED"]
    skipped_not_configured = any(item.get("status") == "SKIPPED_NOT_CONFIGURED" for item in delivery)
    delivery_status = "DELIVERY_FAILED" if failed else ("SUCCESS_WITH_WARNING" if skipped_not_configured else "SUCCESS")
    write_status(
        ctx,
        delivery_status,
        "ENHANCED_REPORT_DELIVERY",
        EXIT_DELIVERY_FAILED if failed else EXIT_SUCCESS,
        {**common_status, "delivery_status": delivery_status, "delivery": delivery},
    )
    return EXIT_DELIVERY_FAILED if failed else EXIT_SUCCESS


def main() -> int:
    args = parse_args()
    ctx = load_context(
        job=args.job,
        config_path=args.config,
        scheduler_config_path=args.scheduler_config,
        trade_date=args.trade_date or None,
        dry_run=args.dry_run,
        preview_existing=args.preview_existing,
        no_telegram=args.no_telegram,
        force=args.force,
        debug=args.debug,
        interactive_broker=args.interactive_broker,
    )

    def execute() -> int:
        try:
            return _run_integrated(args, ctx)
        except Exception as exc:
            rendered = traceback.format_exc()
            trace_path = write_traceback(ctx, "integrated", rendered)
            append_job_log(ctx, "INTEGRATED_RUN_EXCEPTION", rendered)
            if ctx.debug:
                print(rendered, file=sys.stderr, flush=True)
            write_status(ctx, "FAILED", "INTEGRATED_EXCEPTION", EXIT_FAILED, {
                "error": str(exc), "errors": [str(exc)], "traceback_path": trace_path,
            })
            return EXIT_FAILED

    try:
        with FileLock(ctx):
            try:
                needs_resource_lock = ctx.job in {"post_market", "final_watchlist", "full_manual"}
                if needs_resource_lock and ctx.scheduler_config.get("runtime", {}).get("global_resource_lock_enabled", True):
                    lock_name = ctx.scheduler_config.get("locks", {}).get("global_resource_lock_name", "sde_pipeline_write.lock")
                    with FileLock(ctx, str(lock_name), kind="global_resource"):
                        return execute()
                return execute()
            except ResourceLocked as exc:
                write_status(ctx, exc.status, "GLOBAL_RESOURCE_LOCK", EXIT_RESOURCE_LOCKED, {
                    "error": str(exc), "global_resource_lock_status": "BUSY",
                })
                return EXIT_RESOURCE_LOCKED
    except JobAlreadyRunning as exc:
        write_status(ctx, exc.status, "LOCK", EXIT_SKIPPED, {"error": str(exc), "lock_status": "BUSY"})
        return EXIT_SKIPPED


if __name__ == "__main__":
    raise SystemExit(main())
