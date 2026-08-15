#!/usr/bin/env python3
from __future__ import annotations

"""Full Daily orchestration without exposing Broker Summary as a daily report.

Stage order intentionally keeps the existing engines separate:
1. Post Market technical snapshot
2. Market Outlook
3. Final Watchlist through explicit Broker Period Bridge

The Final Watchlist bridge internally runs Broker Summary -> Broker Multi-Day ->
Decision/Exit with one lineage run ID.  Broker Summary and Broker Multi-Day are
internal stages and are not delivered as normal Telegram reports here.
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_final_watchlist_entrypoint import resolve_effective_trade_date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SDE Swing Full Daily with selectable Broker Period")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--period", choices=["1D", "3D", "5D", "CUSTOM", "REUSE"], default="")
    parser.add_argument("--custom-start", default="")
    parser.add_argument("--timeout", type=int, default=-1)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def run(command: list[str], label: str) -> int:
    print("\n" + "=" * 68, flush=True)
    print(f"FULL DAILY - {label}", flush=True)
    print("=" * 68, flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    if completed.returncode != 0:
        print(f"[FAILED] {label} exit code {completed.returncode}", flush=True)
    return int(completed.returncode)


def integrated_job(args: argparse.Namespace, job: str, trade_date: str) -> list[str]:
    runner = "run_sde_job_integrated_market_first.py" if job == "post_market" else "run_sde_job_integrated.py"
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / runner),
        "--job",
        job,
        "--config",
        args.config,
        "--scheduler-config",
        args.scheduler_config,
        "--trade-date",
        trade_date,
    ]
    if args.no_telegram:
        command.append("--no-telegram")
    if args.debug:
        command.append("--debug")
    return command


def final_watchlist_command(args: argparse.Namespace, trade_date: str) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "tools/run_final_watchlist_entrypoint.py"),
        "--config",
        args.config,
        "--scheduler-config",
        args.scheduler_config,
        "--trade-date",
        trade_date,
    ]
    if args.period:
        command.extend(["--period", args.period])
    if args.custom_start:
        command.extend(["--custom-start", args.custom_start])
    if args.timeout >= 0:
        command.extend(["--timeout", str(args.timeout)])
    if args.no_telegram:
        command.append("--no-telegram")
    if args.debug:
        command.append("--debug")
    return command


def resolve_full_daily_trade_date(args: argparse.Namespace) -> str:
    resolver_args = [
        "--config",
        args.config,
        "--scheduler-config",
        args.scheduler_config,
    ]
    if args.trade_date:
        resolver_args.extend(["--trade-date", args.trade_date])
    return resolve_effective_trade_date(resolver_args)


def main() -> int:
    args = parse_args()
    trade_date = resolve_full_daily_trade_date(args)
    print(f"[FULL DAILY] Effective trading date: {trade_date}", flush=True)

    # One effective trading date is shared by all stages so weekend/holiday and
    # pre-data-ready runs cannot mix calendar dates with completed IDX sessions.
    # Explicit --trade-date remains an intentional replay override.
    rc = run(integrated_job(args, "post_market", trade_date), "POST MARKET")
    if rc != 0:
        return rc

    rc = run(integrated_job(args, "market_outlook", trade_date), "MARKET OUTLOOK")
    if rc != 0:
        return rc

    rc = run(final_watchlist_command(args, trade_date), "BROKER PERIOD + FINAL WATCHLIST")
    if rc != 0:
        return rc

    print("\n" + "=" * 68)
    print("FULL DAILY COMPLETE")
    print("Broker Summary/Multi-Day: internal engine stages")
    print("Final Watchlist: primary broker-period output")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
