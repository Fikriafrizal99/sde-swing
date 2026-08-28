#!/usr/bin/env python3
from __future__ import annotations

"""Linux/server wrapper around the canonical Swing scheduler jobs.

It preserves tools/run_scheduled_job.py as the authoritative retry/status layer,
then chains the optional News monitor only after a real successful Market Outlook
or Post Market execution. News remains non-blocking and has no decision-engine
write access.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEDULED_RUN_ROOT = ROOT / "data" / "state" / "scheduler" / "scheduled_runs"

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SDE Swing Linux scheduled job wrapper")
    parser.add_argument(
        "--job",
        required=True,
        choices=("market_outlook", "post_market", "final_watchlist"),
    )
    args, forwarded = parser.parse_known_args(argv)

    scheduler_cmd = [
        sys.executable,
        "-u",
        str(ROOT / "tools" / "run_scheduled_job.py"),
        "--job",
        args.job,
        *forwarded,
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
