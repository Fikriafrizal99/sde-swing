#!/usr/bin/env python3
from __future__ import annotations

"""Preview recent lifecycle events and optionally resend the exact preview.

SQLite is always opened read-only.  The optional resend path sends the exact
preview artifact shown to the operator and never updates telegram_notified_at,
refreshes Yahoo data, or runs an engine scan.
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
from tools.send_lifecycle_digest import build_lifecycle_message


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"SQLite database tidak ditemukan: {resolved}")
    uri_path = quote(resolved.as_posix(), safe="/:.")
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _plain_preview(text: str) -> str:
    text = re.sub(r"</?(?:b|code|pre)>", "", text, flags=re.IGNORECASE)
    return html.unescape(text)


def recent_material_events(db_path: Path, limit: int) -> list[sqlite3.Row]:
    """Return recent material events regardless of Telegram notification state."""
    event_types = sorted(tracker.MATERIAL_LIFECYCLE_EVENT_TYPES)
    placeholders = ",".join("?" for _ in event_types)
    conn = _open_read_only(db_path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if {"signal_recommendation_history", "signal_outcome_ledger"}.issubset(tables):
            query = f"""
                SELECT e.*,
                       (SELECT COUNT(*) FROM signal_recommendation_history h
                        WHERE h.signal_id=e.signal_id) AS recommendation_count,
                       (SELECT s.signal_date FROM signal_outcome_ledger s
                        WHERE s.signal_id=e.signal_id) AS original_signal_date,
                       (SELECT s.trigger_expiry_days FROM signal_outcome_ledger s
                        WHERE s.signal_id=e.signal_id) AS trigger_expiry_days
                FROM lifecycle_events e
                WHERE UPPER(e.event_type) IN ({placeholders})
                ORDER BY COALESCE(e.created_at, e.event_date) DESC, e.event_date DESC, e.symbol DESC
                LIMIT ?
            """
        else:
            # Legacy/read-only preview databases may only contain the original
            # lifecycle_events table.  Preserve preview compatibility without
            # fabricating REC metadata.
            query = f"""
                SELECT e.*
                FROM lifecycle_events e
                WHERE UPPER(e.event_type) IN ({placeholders})
                ORDER BY COALESCE(e.created_at, e.event_date) DESC, e.event_date DESC, e.symbol DESC
                LIMIT ?
            """
        rows = conn.execute(query, [*event_types, max(int(limit), 1)]).fetchall()
    finally:
        conn.close()

    # Formatter reads top-to-bottom. Show the selected recent window in
    # chronological order so lifecycle progression is easier to inspect.
    rows.reverse()
    return rows


def _write_preview(message: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    message_path = output_dir / "LIFECYCLE_DIGEST_PREVIEW.txt"
    message_path.write_text(message, encoding="utf-8")
    return message_path


def _confirm_resend() -> bool:
    try:
        answer = input("\nKirim ulang preview lifecycle ini ke Telegram? [Y/N]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nPengiriman dibatalkan.")
        return False
    return answer.upper() == "Y"


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview recent lifecycle digest without mutating SQLite")
    parser.add_argument("--db", default=str(tracker.DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--output-dir", default=str(tracker.DEFAULT_OUTPUT))
    parser.add_argument("--telegram-config", default="config/telegram.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="After preview, ask whether the exact preview should be resent to Telegram",
    )
    args = parser.parse_args()

    try:
        events = recent_material_events(Path(args.db), args.limit)
    except Exception as exc:
        print(f"[ERROR] Preview lifecycle gagal: {exc}")
        return 2

    if not events:
        _write_preview("", Path(args.output_dir))
        print("Belum ada lifecycle event material untuk dipreview.")
        return 0

    message = build_lifecycle_message(events, max_events=len(events))
    if not message:
        _write_preview("", Path(args.output_dir))
        print("Belum ada lifecycle event material untuk dipreview.")
        return 0

    message_path = _write_preview(message, Path(args.output_dir))

    print("PREVIEW LIFECYCLE TERBARU — SQLite read-only.\n")
    print(_plain_preview(message))
    print(f"\nMenampilkan {len(events)} lifecycle event material terakhir.")
    print(f"Preview tersimpan: {message_path}")

    if not args.confirm_send:
        return 0
    if not _confirm_resend():
        print("Preview tidak dikirim ulang.")
        return 0

    if message.count("<b>") != message.count("</b>") or message.count("<pre>") != message.count("</pre>"):
        print("[ERROR] Lifecycle preview menghasilkan HTML Telegram yang tidak seimbang.")
        return 2

    try:
        tracker.send_telegram(
            message_path,
            Path(args.telegram_config),
            Path(args.scheduler_config),
            False,
        )
    except Exception as exc:
        print(f"[ERROR] Pengiriman ulang lifecycle gagal: {exc}")
        return 2

    print(
        "Preview lifecycle berhasil dikirim ulang ke Telegram. "
        "telegram_notified_at tidak diubah."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
