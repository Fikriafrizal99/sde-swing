#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

from modules.data_sources.yahoo_zapi_validator import validate_yahoo_against_zapi
from modules.job_runner.delivery import deliver
from modules.job_runner.enhanced_runtime_bridge import (
    broker_multiday_payloads,
    broker_summary_payloads,
    final_watchlist_payloads,
    market_outlook_payloads,
    post_market_payloads,
)
from modules.job_runner.reports import write_payloads
from modules.job_runner.report_validation import ReportSourceValidationError, record_validation_error
from modules.job_runner.runtime import (
    EXIT_DELIVERY_FAILED,
    EXIT_SUCCESS,
    append_job_log,
    load_context,
    read_json,
    resolve,
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

RECONCILIATION_JOBS = {"post_market", "final_watchlist", "full_manual"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SDE Swing V1.7 integrated runner: deterministic engine + source validation + Gemini interpretation + Telegram UI"
    )
    parser.add_argument("--job", required=True)
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preview-existing", action="store_true")
    parser.add_argument("--interactive-broker", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--debug", action="store_true")
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


def _watchlist_symbols(ctx) -> list[str]:
    paths = ctx.config.get("paths", {})
    candidate_paths = [
        resolve(paths.get("normalized_watchlist", "modules/historical_downloader/Stockbit_Watchlist_2026-07-19_normalized.csv")),
        resolve(paths.get("broker_navigator_symbols", "data/output/candidates/BROKER_NAVIGATOR_SYMBOLS.csv")),
        resolve("data/output/candidates/broker_symbols.csv"),
    ]
    aliases = {"symbol", "emiten", "ticker", "code", "stockcode"}
    for path in candidate_paths:
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        column = next((col for col in frame.columns if str(col).strip().lower().replace("_", "") in aliases), None)
        if column is None and len(frame.columns):
            column = frame.columns[0]
        if column is None:
            continue
        symbols = [str(item).strip().upper().replace(".JK", "") for item in frame[column].dropna().tolist()]
        symbols = [item for item in dict.fromkeys(symbols) if item]
        if symbols:
            return symbols
    return []


def _run_source_reconciliation(ctx, job: str) -> dict:
    if job not in RECONCILIATION_JOBS:
        return {}
    config = ctx.scheduler_config.get("source_validation", {})
    if not bool(config.get("enabled", True)):
        return {"status": "DISABLED"}
    symbols = _watchlist_symbols(ctx)
    if not symbols:
        result = {"status": "SKIPPED", "reason": "SYMBOL_UNIVERSE_NOT_FOUND"}
        append_job_log(ctx, "YAHOO_ZAPI_RECONCILIATION_SKIPPED", str(result))
        if not bool(config.get("non_blocking", False)):
            reconciliation_config = str(ctx.config.get("data_sources_config", "config/data_sources.json"))
            raise ReportSourceValidationError(
                "source_reconciliation",
                [result["reason"]],
                input_paths=[resolve(ctx.config.get("paths", {}).get("normalized_watchlist", "modules/historical_downloader/Stockbit_Watchlist_2026-07-19_normalized.csv"))],
                source_of_truth=[reconciliation_config],
                details=result,
            )
        return result
    paths = ctx.config.get("paths", {})
    historical_dir = resolve(paths.get("historical_dir", "data/output/historical/by_symbol"))
    output_dir = resolve(config.get("output_dir", "data/output/source_validation"))
    try:
        result = validate_yahoo_against_zapi(
            historical_dir=historical_dir,
            symbols=symbols,
            market_date=ctx.trade_date.isoformat(),
            output_dir=output_dir,
            config_path=ctx.config.get("data_sources_config", "config/data_sources.json"),
            price_tolerance_pct=float(config.get("price_tolerance_pct", 0.005)),
            volume_tolerance_pct=float(config.get("volume_tolerance_pct", 0.20)),
            max_symbols=int(config.get("max_symbols", 0) or 0),
        )
        append_job_log(ctx, "YAHOO_ZAPI_RECONCILIATION", str({
            "status": result.get("status"),
            "validated": result.get("validated"),
            "matched": result.get("matched"),
            "conflicted": result.get("conflicted"),
            "coverage_ratio": result.get("coverage_ratio"),
        }))
        if str(result.get("status", "")).upper().startswith("FAILED") and not bool(config.get("non_blocking", False)):
            reconciliation_config = str(ctx.config.get("data_sources_config", "config/data_sources.json"))
            raise ReportSourceValidationError(
                "source_reconciliation",
                [str(result.get("reason") or result.get("status"))],
                input_paths=[historical_dir],
                source_of_truth=[reconciliation_config],
                details=result,
            )
        return result
    except ReportSourceValidationError:
        raise
    except Exception as exc:
        result = {"status": "FAILED_NON_BLOCKING", "reason": f"{type(exc).__name__}: {exc}"}
        append_job_log(ctx, "YAHOO_ZAPI_RECONCILIATION_FAILED", str(result))
        if not bool(config.get("non_blocking", False)):
            reconciliation_config = str(ctx.config.get("data_sources_config", "config/data_sources.json"))
            raise ReportSourceValidationError(
                "source_reconciliation",
                [result["reason"]],
                input_paths=[historical_dir],
                source_of_truth=[reconciliation_config],
                details=result,
            ) from exc
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
        return broker_summary_payloads(ctx) + _optional_broker_multiday_payloads(ctx) + final_watchlist_payloads(ctx)
    if job == "full_manual":
        global_snapshot, market_status = _load_market_artifacts(ctx)
        return (
            market_outlook_payloads(ctx, global_snapshot, market_status)
            + post_market_payloads(ctx, _load_post_manifest(ctx))
            + broker_summary_payloads(ctx)
            + _optional_broker_multiday_payloads(ctx)
            + final_watchlist_payloads(ctx)
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
        if not insufficient:
            raise
        record_validation_error(ctx, exc)
        append_job_log(ctx, "BROKER_MULTI_DAY_REPORT_SKIPPED", str({
            "errors": exc.errors,
            "input_paths": exc.input_paths,
            "source_of_truth": exc.source_of_truth,
        }))
        return []


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
    engine = subprocess.run(_engine_command(args, ctx.run_id), cwd=Path(__file__).resolve().parent)
    if engine.returncode != 0:
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
        payloads = _enhanced_payloads(ctx, args.job)
        preview_paths = write_payloads(ctx, payloads)
    except ReportSourceValidationError as exc:
        record_validation_error(ctx, exc)
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
    }
    if args.no_telegram:
        write_status(ctx, "SUCCESS", "ENHANCED_REPORT_PREVIEW", EXIT_SUCCESS, common_status)
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
        {**common_status, "delivery": delivery},
    )
    return EXIT_DELIVERY_FAILED if failed else EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
