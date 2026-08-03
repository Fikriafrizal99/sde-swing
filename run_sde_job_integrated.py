#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from modules.global_market.global_market_snapshot import build_global_market_snapshot
from modules.job_runner.delivery import deliver
from modules.job_runner.enhanced_runtime_bridge import (
    broker_multiday_payloads,
    broker_summary_payloads,
    final_watchlist_payloads,
    market_outlook_payloads,
    post_market_payloads,
)
from modules.job_runner.reports import write_payloads
from modules.job_runner.runtime import (
    EXIT_DELIVERY_FAILED,
    EXIT_SUCCESS,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SDE Swing V1.7 integrated runner: deterministic engine + Gemini interpretation + enhanced Telegram UI"
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


def _engine_command(args: argparse.Namespace) -> list[str]:
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
    if not global_snapshot:
        global_snapshot = build_global_market_snapshot(ctx, fallback_to_existing_on_failure=True)
    market_status = read_json(
        ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
    )
    if not market_status:
        market_status = read_json(resolve("data/output/decision/MARKET_STATUS.json"))
    return global_snapshot, market_status


def _load_post_manifest(ctx) -> dict:
    candidates = sorted(resolve("data/output/manifests").glob("SWING_RUN_MANIFEST_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = read_json(path)
        technical_date = str(payload.get("Technical_Date") or payload.get("trade_date") or "")
        if not technical_date or technical_date == ctx.trade_date.isoformat():
            return payload
    snapshot = read_json(resolve("data/output/technical/latest_technical_snapshot.json"))
    return snapshot


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
        return broker_summary_payloads(ctx) + broker_multiday_payloads(ctx) + final_watchlist_payloads(ctx)
    if job == "full_manual":
        global_snapshot, market_status = _load_market_artifacts(ctx)
        return (
            market_outlook_payloads(ctx, global_snapshot, market_status)
            + post_market_payloads(ctx, _load_post_manifest(ctx))
            + broker_summary_payloads(ctx)
            + broker_multiday_payloads(ctx)
            + final_watchlist_payloads(ctx)
        )
    return []


def main() -> int:
    args = parse_args()
    engine = subprocess.run(_engine_command(args), cwd=Path(__file__).resolve().parent)
    if engine.returncode != 0:
        return int(engine.returncode)
    if args.job not in ENHANCED_JOBS:
        return int(engine.returncode)

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
    payloads = _enhanced_payloads(ctx, args.job)
    preview_paths = write_payloads(ctx, payloads)
    if args.no_telegram:
        write_status(ctx, "SUCCESS", "ENHANCED_REPORT_PREVIEW", EXIT_SUCCESS, {
            "preview_paths": [str(path) for path in preview_paths],
            "enhanced_report_count": len(payloads),
            "ai_interpretation": "ENABLED_WITH_DETERMINISTIC_FALLBACK",
        })
        return EXIT_SUCCESS

    delivery = deliver(ctx, payloads)
    failed = [item for item in delivery if item.get("status") == "FAILED"]
    write_status(ctx, "DELIVERY_FAILED" if failed else "SUCCESS", "ENHANCED_REPORT_DELIVERY", EXIT_DELIVERY_FAILED if failed else EXIT_SUCCESS, {
        "preview_paths": [str(path) for path in preview_paths],
        "enhanced_report_count": len(payloads),
        "delivery": delivery,
        "ai_interpretation": "GEMINI_OR_DETERMINISTIC_FALLBACK",
    })
    return EXIT_DELIVERY_FAILED if failed else EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
