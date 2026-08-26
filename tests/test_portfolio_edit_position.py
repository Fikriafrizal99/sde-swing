from __future__ import annotations

import sqlite3
from pathlib import Path

from modules.portfolio.edit_position import edit_position


def test_edit_open_position_rebuilds_plan_when_buy_date_changes(tmp_path: Path) -> None:
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
        CREATE TABLE signal_outcome_ledger (
            signal_id TEXT PRIMARY KEY, symbol TEXT, signal_date TEXT,
            current_status TEXT, exit_date TEXT, entry_price REAL,
            reference_price REAL, stop_loss REAL, take_profit_1 REAL,
            take_profit_2 REAL, setup_type TEXT, raw_decision TEXT,
            score REAL, updated_at TEXT
        );
        CREATE TABLE position_initial_plan (
            position_id TEXT PRIMARY KEY,
            linked_signal_id TEXT,
            symbol TEXT NOT NULL,
            buy_date TEXT NOT NULL,
            buy_price REAL NOT NULL,
            machine_entry_price REAL,
            initial_stop_loss REAL,
            initial_tp1 REAL,
            initial_tp2 REAL,
            initial_setup TEXT,
            initial_decision TEXT,
            initial_score REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE portfolio_edit_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT,
            symbol TEXT, edited_at TEXT, previous_json TEXT, new_json TEXT
        );

        INSERT INTO signal_outcome_ledger VALUES
            ('sig1', 'ENRG', '2026-08-07', 'OPEN', NULL, 600, 600,
             560, 650, 700, 'BREAKOUT', 'BUY', 75, '2026-08-07T16:30:00+07:00');
        INSERT INTO portfolio_positions VALUES
            ('p1', 'sig1', 'ENRG', '2026-08-07', 1000, 600, 'OPEN',
             NULL, NULL, NULL, 'old', '', 'x', 'x');
        INSERT INTO position_initial_plan VALUES
            ('p1', 'sig1', 'ENRG', '2026-08-07', 600, 600,
             560, 650, 700, 'BREAKOUT', 'BUY', 75, 'x');
        """
    )
    conn.commit()

    updated, warnings = edit_position(
        conn,
        position_id="p1",
        status="OPEN",
        quantity=1200,
        buy_price=610,
        buy_date="2026-08-06",
        notes="corrected",
    )
    assert updated["quantity"] == 1200
    assert updated["buy_price"] == 610
    assert updated["buy_date"] == "2026-08-06"

    # The old signal did not exist yet on the corrected BUY date. The cached
    # machine plan must therefore be rebuilt fail-closed instead of preserving
    # the old BREAKOUT/SL/TP snapshot.
    position = conn.execute("SELECT * FROM portfolio_positions WHERE position_id='p1'").fetchone()
    plan = conn.execute("SELECT * FROM position_initial_plan WHERE position_id='p1'").fetchone()
    assert position["signal_id"] is None
    assert plan["buy_price"] == 610
    assert plan["buy_date"] == "2026-08-06"
    assert plan["linked_signal_id"] is None
    assert plan["initial_stop_loss"] is None
    assert plan["initial_tp1"] is None
    assert plan["initial_tp2"] is None
    assert any("linkage dibiarkan kosong" in warning for warning in warnings)
