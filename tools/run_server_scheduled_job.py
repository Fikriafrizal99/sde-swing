#!/usr/bin/env python3
from __future__ import annotations

"""Linux/server wrapper around the canonical Swing scheduler jobs.

It preserves tools/run_scheduled_job.py as the authoritative retry/status layer,
chains the optional News monitor only after a fresh successful Market Outlook or
Post Market execution, prevents an unattended Final Watchlist from falling back
to the legacy manual broker-export wait when Stockbit Playwright is OFF, and
sets the unattended Final Watchlist PRIMARY broker period to 3D. The canonical
broker bridge still captures TODAY as a separate exact 1D pulse.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEDULED_RUN_ROOT = ROOT / "data" / "state" / "scheduler" / "scheduled_runs"
FINAL_WATCHLIST_PRIMARY_PERIOD = "3D"

NEWS_SESSIONS = {
    "market_outlook": "morning",
    "post_market": "post_market",
}


def _run(command: list[str]) -> int:
    completed = subprocess.run(command, cwd=ROOT)
    return int(completed.returncode)


def _latest_classification(job: str) -> str:
    path = SCHEDULED_RUN_ROOT / f"{job}_latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("classification") or "").strip().upper()


def _final_watchlist_server_preflight() -> tuple[bool, str]:
    """Require the unattended Stockbit collector before scheduled Final Watchlist.

    The canonical Final Watchlist intentionally preserves a manual-export fallback
    when Playwright is OFF. That is useful on a desktop, but an unattended server
    would otherwise wait until the broker timeout. Server mode therefore fails
    closed immediately and tells the operator to perform the one-time Stockbit
    login/enable step.
    """
    try:
        from modules.portfolio import stockbit_playwright_collector as collector
    except Exception as exc:
        return False, f"STOCKBIT_PLAYWRIGHT_IMPORT_FAILED:{type(exc).__name__}"

    state = collector.load_state(collector.DEFAULT_STATE)
    if not state.enabled:
        return False, "STOCKBIT_PLAYWRIGHT_DISABLED"
    if not collector.DEFAULT_PROFILE.exists():
        return False, "STOCKBIT_PROFILE_MISSING"
    try:
        has_profile_state = any(collector.DEFAULT_PROFILE.iterdir())
    except OSError:
        has_profile_state = False
    if not has_profile_state:
        return False, "STOCKBIT_PROFILE_EMPTY"
    return True, "READY"


def _server_forwarded_args(job: str, forwarded: list[str]) -> list[str]:
    """Apply server-only defaults without changing the canonical desktop runner."""
    result = list(forwarded)
    if job == "final_watchlist" and "--period" not in result:
        result.extend(["--period", FINAL_WATCHLIST_PRIMARY_PERIOD])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SDE Swing Linux scheduled job wrapper")
    parser.add_argument(
        "--job",
        required=True,
        choices=("market_outlook", "post_market", "final_watchlist"),
    )
    args, forwarded = parser.parse_known_args(argv)

    if args.job == "final_watchlist":
        ready, reason = _final_watchlist_server_preflight()
        if not ready:
            print(
                "[SERVER] Final Watchlist blocked before engine start: "
                f"{reason}. Run the one-time Stockbit Playwright setup/login and enable it.",
                file=sys.stderr,
                flush=True,
            )
            return 1

    server_forwarded = _server_forwarded_args(args.job, forwarded)
    scheduler_cmd = [
        sys.executable,
        "-u",
        str(ROOT / "tools" / "run_scheduled_job.py"),
        "--job",
        args.job,
        *server_forwarded,
    ]
    scheduler_rc = _run(scheduler_cmd)
    if scheduler_rc != 0:
        print(
            f"[SERVER] {args.job} scheduler failed (exit {scheduler_rc}); downstream News skipped.",
            flush=True,
        )
        return scheduler_rc

    session = NEWS_SESSIONS.get(args.job)
    if session is None:
        return 0

    classification = _latest_classification(args.job)
    if classification != "SUCCESS":
        print(
            f"[SERVER] {args.job} classification={classification or 'UNKNOWN'}; "
            "News skipped because the primary job was not a fresh successful execution.",
            flush=True,
        )
        return 0

    news_cmd = [
        sys.executable,
        "-u",
        "-m",
        "modules.news.news_monitor_market_impact",
        "--session",
        session,
        "--telegram",
    ]
    news_rc = _run(news_cmd)
    if news_rc != 0:
        print(
            f"[WARNING] {session} News failed (exit {news_rc}); primary {args.job} remains successful.",
            flush=True,
        )
        return 0

    print(f"[SERVER] {session} News completed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
