import json
import sqlite3

import pytest

from modules.portfolio.edit_position import connect, edit_position


PORTFOLIO_SQL = """
CREATE TABLE portfolio_positions (
    position_id TEXT PRIMARY KEY,
    signal_id TEXT,
    symbol TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    quantity REAL NOT NULL,
    buy_price REAL NOT NULL,
    current_status TEXT NOT NULL DEFAULT 'OPEN',
    sell_date TEXT,
    sell_price REAL,
    realized_return_pct REAL,
    notes TEXT,
    source_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SIGNAL_SQL = """
CREATE TABLE signal_outcome_ledger (
    signal_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    current_status TEXT,
    exit_date TEXT,
    entry_price REAL,
    reference_price REAL,
    stop_loss REAL,
    take_profit_1 REAL,
    take_profit_2 REAL,
    setup_type TEXT,
    raw_decision TEXT,
    score REAL,
    updated_at TEXT
);
"""


def _db(tmp_path):
    conn = connect(tmp_path / "portfolio.db")
    conn.executescript(PORTFOLIO_SQL)
    conn.execute(
        """
        INSERT INTO portfolio_positions (
            position_id, signal_id, symbol, buy_date, quantity, buy_price,
            current_status, sell_date, sell_price, realized_return_pct,
            notes, source_run_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?, ?, '', ?, ?)
        """,
        (
            "MDKA-1",
            None,
            "MDKA",
            "2026-08-10",
            1000,
            2500,
            "2026-08-15",
            2600,
            4.0,
            "salah input",
            "2026-08-10T10:00:00+07:00",
            "2026-08-15T16:30:00+07:00",
        ),
    )
    conn.commit()
    return conn


def test_closed_position_correction_recalculates_realized_return_and_audits(tmp_path):
    conn = _db(tmp_path)
    try:
        updated, warnings = edit_position(
            conn,
            position_id="MDKA-1",
            status="CLOSED",
            buy_price=2400,
            sell_price=2640,
            notes="koreksi transaksi aktual",
        )

        assert updated["current_status"] == "CLOSED"
        assert updated["buy_price"] == 2400
        assert updated["sell_price"] == 2640
        assert updated["realized_return_pct"] == pytest.approx(10.0)
        assert any("snapshot report Position Management historis" in item for item in warnings)

        audit = conn.execute(
            "SELECT previous_json, new_json FROM portfolio_edit_audit WHERE position_id='MDKA-1'"
        ).fetchone()
        assert audit is not None
        previous = json.loads(audit["previous_json"])
        current = json.loads(audit["new_json"])
        assert previous["buy_price"] == 2500
        assert current["buy_price"] == 2400
        assert current["sell_price"] == 2640
    finally:
        conn.close()


def test_closed_position_rejects_sell_date_before_buy_date(tmp_path):
    conn = _db(tmp_path)
    try:
        with pytest.raises(ValueError, match="SELL_DATE_CANNOT_BE_BEFORE_BUY_DATE"):
            edit_position(
                conn,
                position_id="MDKA-1",
                status="CLOSED",
                sell_date="2026-08-09",
            )
    finally:
        conn.close()


def test_closed_symbol_is_not_guessed_when_multiple_closed_lots_exist(tmp_path):
    conn = _db(tmp_path)
    try:
        conn.execute(
            """
            INSERT INTO portfolio_positions (
                position_id, signal_id, symbol, buy_date, quantity, buy_price,
                current_status, sell_date, sell_price, realized_return_pct,
                notes, source_run_id, created_at, updated_at
            ) VALUES ('MDKA-2', NULL, 'MDKA', '2026-08-01', 500, 2300,
                      'CLOSED', '2026-08-05', 2350, 2.173913, '', '', ?, ?)
            """,
            ("2026-08-01T10:00:00+07:00", "2026-08-05T16:30:00+07:00"),
        )
        conn.commit()

        with pytest.raises(ValueError, match="MULTIPLE_CLOSED_POSITIONS_USE_POSITION_ID"):
            edit_position(conn, symbol="MDKA", status="CLOSED", notes="ambiguous")
    finally:
        conn.close()


def test_corrected_buy_date_rejects_future_signal_link_for_closed_position(tmp_path):
    conn = _db(tmp_path)
    try:
        conn.executescript(SIGNAL_SQL)
        conn.execute(
            """
            INSERT INTO signal_outcome_ledger (
                signal_id, symbol, signal_date, current_status, exit_date,
                entry_price, reference_price, stop_loss, take_profit_1,
                take_profit_2, setup_type, raw_decision, score, updated_at
            ) VALUES ('SIG-FUTURE', 'MDKA', '2026-08-10', 'CLOSED', '2026-08-20',
                      2500, 2500, 2350, 2750, 2900, 'BREAKOUT', 'BUY', 80,
                      '2026-08-20T16:30:00+07:00')
            """
        )
        conn.execute("UPDATE portfolio_positions SET signal_id='SIG-FUTURE' WHERE position_id='MDKA-1'")
        conn.executescript(
            """
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
            """
        )
        conn.execute(
            """
            INSERT INTO position_initial_plan VALUES (
                'MDKA-1', 'SIG-FUTURE', 'MDKA', '2026-08-10', 2500,
                2500, 2350, 2750, 2900, 'BREAKOUT', 'BUY', 80,
                '2026-08-10T10:00:00+07:00'
            )
            """
        )
        conn.commit()

        updated, _ = edit_position(
            conn,
            position_id="MDKA-1",
            status="CLOSED",
            buy_date="2026-08-08",
        )
        assert updated["buy_date"] == "2026-08-08"

        position = conn.execute(
            "SELECT signal_id FROM portfolio_positions WHERE position_id='MDKA-1'"
        ).fetchone()
        plan = conn.execute(
            "SELECT linked_signal_id, initial_setup FROM position_initial_plan WHERE position_id='MDKA-1'"
        ).fetchone()
        assert position["signal_id"] is None
        assert plan["linked_signal_id"] is None
        assert plan["initial_setup"] is None
    finally:
        conn.close()
