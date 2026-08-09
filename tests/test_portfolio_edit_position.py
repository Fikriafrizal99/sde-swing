from __future__ import annotations

import sqlite3
from pathlib import Path

from modules.portfolio.edit_position import edit_open_position


def test_edit_open_position_updates_actual_buy_facts_without_touching_targets(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE portfolio_positions (
            position_id TEXT PRIMARY KEY, signal_id TEXT, symbol TEXT, buy_date TEXT,
            quantity REAL, buy_price REAL, current_status TEXT, sell_date TEXT,
            sell_price REAL, realized_return_pct REAL, notes TEXT, source_run_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE position_initial_plan (
            position_id TEXT PRIMARY KEY, buy_date TEXT, buy_price REAL,
            initial_stop_loss REAL, initial_tp1 REAL, initial_tp2 REAL
        );
        INSERT INTO portfolio_positions VALUES
            ('p1', '', 'ENRG', '2026-08-07', 1000, 600, 'OPEN', NULL, NULL, NULL, 'old', '', 'x', 'x');
        INSERT INTO position_initial_plan VALUES
            ('p1', '2026-08-07', 600, 560, 650, 700);
        CREATE TABLE portfolio_edit_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT,
            symbol TEXT, edited_at TEXT, previous_json TEXT, new_json TEXT
        );
        """
    )
    conn.commit()

    updated, warnings = edit_open_position(
        conn,
        position_id="p1",
        quantity=1200,
        buy_price=610,
        buy_date="2026-08-06",
        notes="corrected",
    )
    assert updated["quantity"] == 1200
    assert updated["buy_price"] == 610
    assert updated["buy_date"] == "2026-08-06"
    plan = conn.execute("SELECT * FROM position_initial_plan WHERE position_id='p1'").fetchone()
    assert plan["buy_price"] == 610
    assert plan["buy_date"] == "2026-08-06"
    assert plan["initial_stop_loss"] == 560
    assert plan["initial_tp1"] == 650
    assert plan["initial_tp2"] == 700
    assert warnings == []
