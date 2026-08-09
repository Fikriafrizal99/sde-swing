#!/usr/bin/env python3
from __future__ import annotations

"""Read-only preview of recent lifecycle events using the live Telegram formatter.

This utility intentionally opens SQLite with mode=ro and never sends Telegram,
updates telegram_notified_at, refreshes Yahoo data, or runs an engine scan.
"""

import argparse
import html
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.analytics import outcome_tracker as tracker


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"SQLite database tidak ditemukan: {resolved}")
    uri_path = quote(resolved.as_posix(), safe="/:.")
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _plain_preview(text: str) -> str:
    text = re.sub(r"</?b>", "", text, flags=re.IGNORECASE)
    return html.unescape(text)


def recent_material_events(db_path: Path, limit: int) -> list[sqlite3.Row]:
    event_types = sorted(tracker.MATERIAL_LIFECYCLE_EVENT_TYPES)
    placeholders = ",".join("?" for _ in event_types)
    conn = _open_read_only(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM lifecycle_events
            WHERE UPPER(event_type) IN ({placeholders})
            ORDER BY COALESCE(created_at, event_date) DESC, event_date DESC, symbol DESC
            LIMIT ?
            """,
            [*event_types, max(int(limit), 1)],
        ).fetchall()
    finally:
        conn.close()

    # Formatter reads top-to-bottom. Show the selected recent window in
    # chronological order so lifecycle progression is easier to inspect.
    rows.reverse()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview recent lifecycle digest without mutating SQLite")
    parser.add_argument("--db", default=str(tracker.DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    try:
        events = recent_material_events(Path(args.db), args.limit)
    except Exception as exc:
        print(f"[ERROR] Preview lifecycle gagal: {exc}")
        return 2

    if not events:
        print("Belum ada lifecycle event material untuk dipreview.")
        return 0

    message = tracker._status_changes_telegram(events, max_events=len(events))
    if not message:
        print("Belum ada lifecycle event material untuk dipreview.")
        return 0

    print("PREVIEW ONLY — SQLite read-only, tidak mengirim Telegram.\n")
    print(_plain_preview(message))
    print(f"\nMenampilkan {len(events)} lifecycle event material terakhir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
