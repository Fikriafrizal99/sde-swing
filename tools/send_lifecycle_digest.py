#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.analytics import outcome_tracker as tracker
from modules.analytics.lifecycle_presentation import build_lifecycle_message as _build_lifecycle_message
from modules.branding import apply_ftj_branding


def build_lifecycle_message(*args, **kwargs) -> str:
    """Canonical lifecycle formatter plus the FTJ human-facing title."""
    return apply_ftj_branding(_build_lifecycle_message(*args, **kwargs))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send canonical lifecycle digest")
    parser.add_argument("--db", default=str(tracker.DEFAULT_DB))
    parser.add_argument("--output-dir", default=str(tracker.DEFAULT_OUTPUT))
    parser.add_argument("--telegram-config", default="config/telegram.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--max-events", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    message_path = output_dir / "LIFECYCLE_DIGEST_TELEGRAM.txt"

    conn = tracker.connect(db_path)
    try:
        events = tracker.material_lifecycle_events(tracker.pending_lifecycle_events(conn))
    finally:
        conn.close()

    limit = max(int(args.max_events or 1), 1)
    message = build_lifecycle_message(events, max_events=limit)
    message_path.write_text(message, encoding="utf-8")

    if not events:
        print("Tidak ada perubahan lifecycle material. Telegram tidak dikirim.")
        return 0
    if message.count("<b>") != message.count("</b>") or message.count("<pre>") != message.count("</pre>"):
        raise RuntimeError("Lifecycle Digest menghasilkan HTML Telegram yang tidak seimbang.")
    if args.dry_run:
        print(message)
        return 0

    tracker.send_telegram(
        message_path,
        Path(args.telegram_config),
        Path(args.scheduler_config),
        False,
    )
    event_ids = [tracker.norm_text(event["event_id"]) for event in events[:limit]]
    marked = tracker.mark_lifecycle_events_notified(db_path, event_ids)
    print(f"Lifecycle digest terkirim: {len(events)} event, acknowledged={marked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
