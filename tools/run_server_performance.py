#!/usr/bin/env python3
from __future__ import annotations

"""Server-only Performance/Outcome reporting workflow.

This job runs after Active Portfolio Management and deliberately does not invoke
Technical, Broker Fusion, Decision Engine, Final Watchlist, Entry, SL, or TP
engines. It only refreshes the existing outcome tracker from local EOD history,
rebuilds performance artifacts, then sends the canonical Performance report to
Telegram.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSITION_SERVICE = "sde-swing-position-management.service"
WAIT_SECONDS = 45 * 60
POLL_SECONDS = 30


def _run(command: list[str]) -> int:
    return int(subprocess.run(command, cwd=ROOT).returncode)


def _position_management_active() -> bool:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    result = subprocess.run(
        [systemctl, "is-active", "--quiet", POSITION_SERVICE],
        cwd=ROOT,
    )
    return int(result.returncode) == 0


def _wait_for_position_management() -> bool:
    if not _position_management_active():
        return True

    print(
        "[PERFORMANCE] Position Management masih aktif; menunggu sebelum refresh outcome.",
        flush=True,
    )
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        if not _position_management_active():
            print("[PERFORMANCE] Position Management selesai; melanjutkan.", flush=True)
            return True

    print(
        "[PERFORMANCE] Gagal mulai: Position Management masih aktif setelah 45 menit.",
        file=sys.stderr,
        flush=True,
    )
    return False


def main() -> int:
    if not _wait_for_position_management():
        return 1

    print("[1/2] Refresh outcome tracker dan rebuild performance...", flush=True)
    sync_rc = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.analytics.outcome_tracker",
            "sync",
        ]
    )
    if sync_rc != 0:
        print(
            f"[FAILED] Performance sync gagal. Exit code {sync_rc}.",
            file=sys.stderr,
            flush=True,
        )
        return sync_rc or 1

    print("[2/2] Mengirim Performance Report ke Telegram Evaluation...", flush=True)
    telegram_rc = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.analytics.outcome_tracker",
            "telegram",
        ]
    )
    if telegram_rc != 0:
        print(
            f"[FAILED] Performance Telegram gagal. Exit code {telegram_rc}.",
            file=sys.stderr,
            flush=True,
        )
        return telegram_rc or 1

    print("[OK] Performance refresh + Telegram selesai.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
