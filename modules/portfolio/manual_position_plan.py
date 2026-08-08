#!/usr/bin/env python3
from __future__ import annotations

"""Manual initial-plan support for actual portfolio positions.

This module is additive and portfolio-only. It never edits a machine-generated
signal plan. Manual values are stored separately and may only fill blank fields
inside ``position_initial_plan`` for positions that do not already have engine
levels.
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import normalize_symbol

DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_manual_initial_plan (
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
CREATE INDEX IF NOT EXISTS idx_portfolio_manual_plan_symbol
    ON portfolio_manual_initial_plan(symbol, buy_date);
"""


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    if number <= 0:
        raise ValueError("PLAN_LEVEL_MUST_BE_POSITIVE")
    return number


def resolve_open_position(
    conn: sqlite3.Connection,
    *,
    position_id: str = "",
    symbol: str = "",
    buy_date: str = "",
    quantity: float | None = None,
    buy_price: float | None = None,
) -> sqlite3.Row:
    if position_id:
        row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE position_id=? AND UPPER(current_status)='OPEN'",
            (position_id.strip(),),
        ).fetchone()
        if not row:
            raise ValueError("OPEN_PORTFOLIO_POSITION_NOT_FOUND")
        return row

    normalized = normalize_symbol(symbol)
    if not normalized:
        raise ValueError("SYMBOL_REQUIRED")
    clauses = ["UPPER(symbol)=?", "UPPER(current_status)='OPEN'"]
    params: list[Any] = [normalized]
    if buy_date:
        clauses.append("buy_date=?")
        params.append(buy_date[:10])
    if quantity is not None:
        clauses.append("ABS(quantity-?) < 0.0000001")
        params.append(float(quantity))
    if buy_price is not None:
        clauses.append("ABS(buy_price-?) < 0.0000001")
        params.append(float(buy_price))
    row = conn.execute(
        "SELECT * FROM portfolio_positions WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC LIMIT 1",
        params,
    ).fetchone()
    if not row:
        raise ValueError("OPEN_PORTFOLIO_POSITION_NOT_FOUND")
    return row


def validate_levels(buy_price: float, stop: float | None, tp1: float | None, tp2: float | None) -> None:
    if stop is not None and stop >= buy_price:
        raise ValueError("INITIAL_SL_MUST_BE_BELOW_BUY_PRICE")
    if tp1 is not None and tp1 <= buy_price:
        raise ValueError("TP1_MUST_BE_ABOVE_BUY_PRICE")
    if tp2 is not None and tp2 <= buy_price:
        raise ValueError("TP2_MUST_BE_ABOVE_BUY_PRICE")
    if tp1 is not None and tp2 is not None and tp2 <= tp1:
        raise ValueError("TP2_MUST_BE_ABOVE_TP1")


def set_manual_plan(
    conn: sqlite3.Connection,
    *,
    position_id: str = "",
    symbol: str = "",
    buy_date: str = "",
    quantity: float | None = None,
    buy_price: float | None = None,
    stop_loss: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    setup: str = "MANUAL",
) -> str:
    conn.executescript(SCHEMA_SQL)
    row = resolve_open_position(
        conn,
        position_id=position_id,
        symbol=symbol,
        buy_date=buy_date,
        quantity=quantity,
        buy_price=buy_price,
    )
    stop = _as_float(stop_loss)
    target1 = _as_float(tp1)
    target2 = _as_float(tp2)
    actual_buy = float(row["buy_price"])
    validate_levels(actual_buy, stop, target1, target2)
    timestamp = now_text()
    setup_text = (setup or "MANUAL").strip().upper()
    conn.execute(
        """
        INSERT INTO portfolio_manual_initial_plan (
            position_id, symbol, buy_date, initial_stop_loss, initial_tp1,
            initial_tp2, setup, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            initial_stop_loss=excluded.initial_stop_loss,
            initial_tp1=excluded.initial_tp1,
            initial_tp2=excluded.initial_tp2,
            setup=excluded.setup,
            updated_at=excluded.updated_at
        """,
        (
            row["position_id"], normalize_symbol(row["symbol"]), row["buy_date"],
            stop, target1, target2, setup_text, timestamp, timestamp,
        ),
    )
    apply_manual_plan_to_initial_plan(conn, str(row["position_id"]))
    conn.commit()
    return str(row["position_id"])


def apply_manual_plan_to_initial_plan(conn: sqlite3.Connection, position_id: str) -> dict[str, Any] | None:
    """Fill only missing initial-plan fields; never overwrite machine levels."""
    conn.executescript(SCHEMA_SQL)
    manual = conn.execute(
        "SELECT * FROM portfolio_manual_initial_plan WHERE position_id=?",
        (position_id,),
    ).fetchone()
    if not manual:
        row = conn.execute(
            "SELECT * FROM position_initial_plan WHERE position_id=?",
            (position_id,),
        ).fetchone()
        return dict(row) if row else None

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='position_initial_plan'"
    ).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE position_initial_plan
            SET initial_stop_loss=COALESCE(initial_stop_loss, ?),
                initial_tp1=COALESCE(initial_tp1, ?),
                initial_tp2=COALESCE(initial_tp2, ?),
                initial_setup=CASE
                    WHEN initial_setup IS NULL OR TRIM(initial_setup)='' THEN ?
                    ELSE initial_setup
                END
            WHERE position_id=?
            """,
            (
                manual["initial_stop_loss"], manual["initial_tp1"], manual["initial_tp2"],
                manual["setup"], position_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM position_initial_plan WHERE position_id=?",
            (position_id,),
        ).fetchone()
        return dict(row) if row else None
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain manual initial plan for actual OPEN portfolio")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    set_parser = sub.add_parser("set")
    set_parser.add_argument("--position-id", default="")
    set_parser.add_argument("--symbol", default="")
    set_parser.add_argument("--buy-date", default="")
    set_parser.add_argument("--quantity", type=float, default=None)
    set_parser.add_argument("--buy-price", type=float, default=None)
    set_parser.add_argument("--sl", type=float, default=None)
    set_parser.add_argument("--tp1", type=float, default=None)
    set_parser.add_argument("--tp2", type=float, default=None)
    set_parser.add_argument("--setup", default="MANUAL")
    show = sub.add_parser("show")
    show.add_argument("--symbol", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn = connect(Path(args.db))
    try:
        if args.command == "set":
            position_id = set_manual_plan(
                conn,
                position_id=args.position_id,
                symbol=args.symbol,
                buy_date=args.buy_date,
                quantity=args.quantity,
                buy_price=args.buy_price,
                stop_loss=args.sl,
                tp1=args.tp1,
                tp2=args.tp2,
                setup=args.setup,
            )
            print(f"Manual initial plan tersimpan: position_id={position_id}")
            return 0
        normalized = normalize_symbol(args.symbol)
        where = " WHERE UPPER(symbol)=?" if normalized else ""
        params = (normalized,) if normalized else ()
        rows = conn.execute(
            "SELECT * FROM portfolio_manual_initial_plan" + where + " ORDER BY buy_date DESC, symbol",
            params,
        ).fetchall()
        if not rows:
            print("Belum ada manual initial plan.")
        else:
            for row in rows:
                print(dict(row))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
