from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.portfolio.manual_position_plan import (
    apply_manual_plan_to_initial_plan,
    freeze_position_initial_plan,
)


def _conn(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "test.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
    )
    return conn


def _insert_signal(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    signal_date: str,
    setup: str,
    stop: float,
    tp1: float,
    tp2: float,
    status: str = "OPEN",
    exit_date: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO signal_outcome_ledger (
            signal_id, symbol, signal_date, current_status, exit_date,
            entry_price, reference_price, stop_loss, take_profit_1,
            take_profit_2, setup_type, raw_decision, score, updated_at
        ) VALUES (?, 'TPIA', ?, ?, ?, 10000, 9950, ?, ?, ?, ?, 'BUY', 80, ?)
        """,
        (signal_id, signal_date, status, exit_date, stop, tp1, tp2, setup, signal_date),
    )


def _insert_position(conn: sqlite3.Connection, signal_id: str | None) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_positions (
            position_id, signal_id, symbol, buy_date, quantity, buy_price,
            current_status, created_at, updated_at
        ) VALUES ('POS-TPIA', ?, 'TPIA', '2026-08-10', 100, 10050,
                  'OPEN', '2026-08-10T09:30:00+07:00', '2026-08-10T09:30:00+07:00')
        """,
        (signal_id,),
    )
    conn.commit()


def test_legacy_future_signal_is_rejected_and_manual_plan_wins() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(Path(tmp))
        _insert_signal(
            conn,
            signal_id="FUTURE",
            signal_date="2026-08-20",
            setup="BREAKOUT",
            stop=9700,
            tp1=10800,
            tp2=11500,
        )
        _insert_position(conn, "FUTURE")

        # Simulate the old bug: maintenance already froze a future signal into
        # a position that was actually bought ten days earlier.
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
            CREATE TABLE portfolio_manual_initial_plan (
                position_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                buy_date TEXT NOT NULL,
                initial_stop_loss REAL,
                initial_tp1 REAL,
                initial_tp2 REAL,
                setup TEXT NOT NULL DEFAULT 'MANUAL',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO position_initial_plan VALUES (
                'POS-TPIA','FUTURE','TPIA','2026-08-10',10050,10000,
                9700,10800,11500,'BREAKOUT','BUY',80,'2026-08-20T16:00:00+07:00'
            );
            INSERT INTO portfolio_manual_initial_plan VALUES (
                'POS-TPIA','TPIA','2026-08-10',9800,10600,11200,
                'PULLBACK','2026-08-10T09:31:00+07:00','2026-08-10T09:31:00+07:00'
            );
            """
        )

        plan = apply_manual_plan_to_initial_plan(conn, "POS-TPIA")
        assert plan is not None
        assert not plan["linked_signal_id"]
        assert plan["initial_setup"] == "PULLBACK"
        assert plan["initial_stop_loss"] == 9800
        assert plan["initial_tp1"] == 10600
        assert plan["initial_tp2"] == 11200

        position = conn.execute(
            "SELECT signal_id FROM portfolio_positions WHERE position_id='POS-TPIA'"
        ).fetchone()
        assert position["signal_id"] is None

        repair = conn.execute(
            "SELECT reason, previous_signal_id, new_signal_id FROM portfolio_signal_link_repairs"
        ).fetchone()
        assert repair is not None
        assert repair["reason"] == "FUTURE_SIGNAL_LINK_REJECTED"
        assert repair["previous_signal_id"] == "FUTURE"
        assert repair["new_signal_id"] is None
        conn.close()


def test_freeze_selects_latest_signal_that_was_valid_on_buy_date() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(Path(tmp))
        _insert_signal(
            conn,
            signal_id="VALID-ASOF",
            signal_date="2026-08-08",
            setup="PULLBACK",
            stop=9800,
            tp1=10600,
            tp2=11200,
            status="CLOSED",
            exit_date="2026-08-15",
        )
        _insert_signal(
            conn,
            signal_id="FUTURE",
            signal_date="2026-08-20",
            setup="BREAKOUT",
            stop=9700,
            tp1=10800,
            tp2=11500,
        )
        _insert_position(conn, "FUTURE")

        plan = freeze_position_initial_plan(conn, "POS-TPIA")
        assert plan is not None
        assert plan["linked_signal_id"] == "VALID-ASOF"
        assert plan["initial_setup"] == "PULLBACK"
        assert plan["initial_stop_loss"] == 9800
        assert plan["initial_tp1"] == 10600
        assert plan["initial_tp2"] == 11200

        position = conn.execute(
            "SELECT signal_id FROM portfolio_positions WHERE position_id='POS-TPIA'"
        ).fetchone()
        assert position["signal_id"] == "VALID-ASOF"
        conn.close()
