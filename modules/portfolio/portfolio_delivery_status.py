#!/usr/bin/env python3
from __future__ import annotations

"""Print the latest Telegram delivery result for Position Management only.

Read-only helper used by the manual portfolio launcher. It does not mutate
Telegram idempotency, delivery routing, engine state, or report content.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = PROJECT_ROOT / "data/state/scheduler/delivery_log.jsonl"
TERMINAL_STATUSES = {
    "SENT",
    "FAILED",
    "DUPLICATE_SUPPRESSED",
    "SKIPPED_NOT_CONFIGURED",
    "NO_TELEGRAM",
    "DRY_RUN",
}


def latest_position_delivery(path: Path = DEFAULT_LOG) -> dict | None:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("job") or "").lower() != "position_management":
            continue
        if str(row.get("report_type") or "").upper() != "POSITION_MANAGEMENT":
            continue
        if str(row.get("status") or "").upper() not in TERMINAL_STATUSES:
            continue
        return row
    return None


def main() -> int:
    row = latest_position_delivery()
    if row is None:
        print("Telegram delivery status: UNKNOWN (delivery log belum tersedia)")
        return 0

    status = str(row.get("status") or "UNKNOWN").upper()
    topic = str(row.get("target_thread") or row.get("category") or "report")
    thread_id = str(row.get("message_thread_id") or "").strip()
    message_ids = row.get("telegram_message_ids") or []
    suffix = f" | thread_id={thread_id}" if thread_id else ""
    if status == "SENT":
        ids = ",".join(str(item) for item in message_ids if item not in (None, "")) or "-"
        print(f"Telegram delivery status: SENT -> topic {topic}{suffix} | message_id={ids}")
        return 0
    if status == "DUPLICATE_SUPPRESSED":
        print(f"Telegram delivery status: DUPLICATE_SUPPRESSED -> tidak dikirim ulang{suffix}")
        return 0
    if status == "SKIPPED_NOT_CONFIGURED":
        print("Telegram delivery status: SKIPPED_NOT_CONFIGURED -> cek token/chat ID")
        return 0
    if status in {"NO_TELEGRAM", "DRY_RUN"}:
        print(f"Telegram delivery status: {status} -> tidak ada pengiriman")
        return 0

    error = str(row.get("error") or "unknown error")
    print(f"Telegram delivery status: FAILED -> {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
