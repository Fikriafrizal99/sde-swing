from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

from tools import preview_lifecycle_digest as preview


ROOT = Path(__file__).resolve().parents[1]


def test_recent_preview_includes_already_notified_material_event(tmp_path: Path) -> None:
    db_path = tmp_path / "lifecycle.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE lifecycle_events (
                event_id TEXT,
                symbol TEXT,
                event_type TEXT,
                event_date TEXT,
                created_at TEXT,
                telegram_notified_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO lifecycle_events
            (event_id, symbol, event_type, event_date, created_at, telegram_notified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "EVT-1",
                "LSIP",
                "TP1_HIT",
                "2026-08-10",
                "2026-08-10T17:00:00+07:00",
                "2026-08-10T17:05:00+07:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    rows = preview.recent_material_events(db_path, 8)

    assert len(rows) == 1
    assert rows[0]["event_id"] == "EVT-1"
    assert rows[0]["telegram_notified_at"]


def test_preview_resend_never_marks_lifecycle_notified() -> None:
    source = inspect.getsource(preview)
    assert "mark_lifecycle_events_notified" not in source
    assert "send_telegram" in source


def test_menu_13_previews_then_offers_resend() -> None:
    menu = (ROOT / "maintenance/PERFORMANCE_MENU.bat").read_text(encoding="utf-8-sig")

    assert "[13] Preview lifecycle terbaru + opsi kirim ulang" in menu
    assert "tools\\preview_lifecycle_digest.py --limit 8 --confirm-send" in menu
    assert "tidak mengubah telegram_notified_at" in menu
