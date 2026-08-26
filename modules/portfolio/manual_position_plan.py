#!/usr/bin/env python3
from __future__ import annotations

"""Manual and frozen initial-plan support for actual portfolio positions.

Actual portfolio positions must never inherit a machine signal that did not
exist yet on the user's buy date.  This module therefore owns a portfolio-only
temporal guard:

* freeze the machine initial plan immediately after an actual BUY is recorded;
* only link signals that are valid as-of the actual ``buy_date``;
* repair legacy/future signal links deterministically during maintenance;
* never guess from a newer same-symbol signal when no historical match exists;
* let manual values fill blanks without overwriting a valid machine plan.

The signal lifecycle itself remains untouched.  Only the actual portfolio link
and ``position_initial_plan`` snapshot are repaired/frozen.
"""

import argparse
import hashlib
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

CREATE TABLE IF NOT EXISTS position_initial_plan (
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

CREATE TABLE IF NOT EXISTS portfolio_signal_link_repairs (
    repair_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    previous_signal_id TEXT,
    new_signal_id TEXT,
    reason TEXT NOT NULL,
    repaired_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_signal_link_repairs_position
    ON portfolio_signal_link_repairs(position_id, repaired_at);
"""

TERMINAL_SIGNAL_STATUSES = {"CLOSED", "EXPIRED", "INVALIDATED_BEFORE_ENTRY"}


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


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


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


def _signal_terminal_date(signal: sqlite3.Row) -> str:
    status = str(signal["current_status"] or "").strip().upper() if "current_status" in signal.keys() else ""
    exit_date = _date_text(signal["exit_date"] if "exit_date" in signal.keys() else "")
    if exit_date:
        return exit_date
    if status in TERMINAL_SIGNAL_STATUSES:
        return _date_text(signal["signal_date"] if "signal_date" in signal.keys() else "")
    return ""


def _signal_valid_as_of(signal: sqlite3.Row | None, symbol: str, buy_date: str) -> bool:
    if signal is None:
        return False
    if normalize_symbol(signal["symbol"] if "symbol" in signal.keys() else "") != normalize_symbol(symbol):
        return False
    signal_date = _date_text(signal["signal_date"] if "signal_date" in signal.keys() else "")
    if not signal_date or not buy_date or signal_date > buy_date:
        return False
    status = str(signal["current_status"] or "").strip().upper() if "current_status" in signal.keys() else ""
    if status == "INVALID_DATA":
        return False
    terminal_date = _signal_terminal_date(signal)
    if terminal_date and terminal_date < buy_date:
        return False
    if status in {"EXPIRED", "INVALIDATED_BEFORE_ENTRY"} and terminal_date == buy_date:
        return False
    return True


def _signal_by_id(conn: sqlite3.Connection, signal_id: str) -> sqlite3.Row | None:
    if not signal_id or not _table_exists(conn, "signal_outcome_ledger"):
        return None
    return conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE signal_id=?",
        (signal_id,),
    ).fetchone()


def _best_signal_as_of(conn: sqlite3.Connection, symbol: str, buy_date: str) -> sqlite3.Row | None:
    if not _table_exists(conn, "signal_outcome_ledger") or not buy_date:
        return None
    rows = conn.execute(
        """
        SELECT * FROM signal_outcome_ledger
        WHERE UPPER(symbol)=? AND signal_date<=?
        ORDER BY signal_date DESC, updated_at DESC, signal_id DESC
        """,
        (normalize_symbol(symbol), buy_date),
    ).fetchall()
    for row in rows:
        if _signal_valid_as_of(row, symbol, buy_date):
            return row
    return None


def _machine_plan(signal: sqlite3.Row | None) -> dict[str, Any]:
    if signal is None:
        return {
            "linked_signal_id": None,
            "machine_entry_price": None,
            "initial_stop_loss": None,
            "initial_tp1": None,
            "initial_tp2": None,
            "initial_setup": None,
            "initial_decision": None,
            "initial_score": None,
        }
    return {
        "linked_signal_id": str(signal["signal_id"] or ""),
        "machine_entry_price": (
            _safe_float(signal["entry_price"] if "entry_price" in signal.keys() else None)
            or _safe_float(signal["reference_price"] if "reference_price" in signal.keys() else None)
        ),
        "initial_stop_loss": _safe_float(signal["stop_loss"] if "stop_loss" in signal.keys() else None),
        "initial_tp1": _safe_float(signal["take_profit_1"] if "take_profit_1" in signal.keys() else None),
        "initial_tp2": _safe_float(signal["take_profit_2"] if "take_profit_2" in signal.keys() else None),
        "initial_setup": str(signal["setup_type"] or "").strip() if "setup_type" in signal.keys() else None,
        "initial_decision": str(signal["raw_decision"] or "").strip() if "raw_decision" in signal.keys() else None,
        "initial_score": _safe_float(signal["score"] if "score" in signal.keys() else None),
    }


def _log_link_repair(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    symbol: str,
    buy_date: str,
    previous_signal_id: str,
    new_signal_id: str,
    reason: str,
) -> None:
    material = "|".join([
        position_id,
        normalize_symbol(symbol),
        buy_date,
        previous_signal_id,
        new_signal_id,
        reason,
    ])
    repair_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    conn.execute(
        """
        INSERT OR IGNORE INTO portfolio_signal_link_repairs (
            repair_id, position_id, symbol, buy_date, previous_signal_id,
            new_signal_id, reason, repaired_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repair_id,
            position_id,
            normalize_symbol(symbol),
            buy_date,
            previous_signal_id or None,
            new_signal_id or None,
            reason,
            now_text(),
        ),
    )


def freeze_position_initial_plan(conn: sqlite3.Connection, position_id: str) -> dict[str, Any] | None:
    """Freeze/repair the portfolio plan using only evidence valid on buy_date.

    A valid explicit/previous link is preserved.  If that link points to a
    future signal or a lifecycle that ended before the actual buy date, it is
    rejected.  Manual portfolio plans take precedence over auto-relinking when
    such an invalid legacy link is repaired.  Without a manual plan, the latest
    eligible signal as-of the buy date is used.  If none exists, machine plan
    fields remain blank rather than borrowing a future signal.
    """
    conn.executescript(SCHEMA_SQL)
    if not _table_exists(conn, "portfolio_positions"):
        return None
    position = conn.execute(
        "SELECT * FROM portfolio_positions WHERE position_id=?",
        (position_id,),
    ).fetchone()
    if not position:
        return None

    symbol = normalize_symbol(position["symbol"])
    buy_date = _date_text(position["buy_date"])
    existing = conn.execute(
        "SELECT * FROM position_initial_plan WHERE position_id=?",
        (position_id,),
    ).fetchone()
    manual = conn.execute(
        "SELECT * FROM portfolio_manual_initial_plan WHERE position_id=?",
        (position_id,),
    ).fetchone()

    position_signal_id = str(position["signal_id"] or "").strip() if "signal_id" in position.keys() else ""
    existing_signal_id = str(existing["linked_signal_id"] or "").strip() if existing else ""
    current_signal_id = position_signal_id or existing_signal_id
    current_signal = _signal_by_id(conn, current_signal_id)
    current_valid = _signal_valid_as_of(current_signal, symbol, buy_date)

    # A previously frozen, temporally valid plan is immutable.
    if existing is not None and current_valid and existing_signal_id == current_signal_id:
        if position_signal_id != current_signal_id:
            conn.execute(
                "UPDATE portfolio_positions SET signal_id=?, updated_at=? WHERE position_id=?",
                (current_signal_id, now_text(), position_id),
            )
            conn.commit()
        return dict(existing)

    needs_repair = bool(current_signal_id and not current_valid)
    target_signal: sqlite3.Row | None = current_signal if current_valid else None

    # If a user already supplied a manual initial plan, never replace an invalid
    # historical link with a guessed machine signal.  Clear the bad machine
    # snapshot first so the manual values can become authoritative.
    if target_signal is None and manual is None:
        target_signal = _best_signal_as_of(conn, symbol, buy_date)

    plan = _machine_plan(target_signal)
    new_signal_id = str(plan["linked_signal_id"] or "")
    timestamp = now_text()

    if existing is None:
        conn.execute(
            """
            INSERT INTO position_initial_plan (
                position_id, linked_signal_id, symbol, buy_date, buy_price,
                machine_entry_price, initial_stop_loss, initial_tp1, initial_tp2,
                initial_setup, initial_decision, initial_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                plan["linked_signal_id"],
                symbol,
                buy_date,
                float(position["buy_price"]),
                plan["machine_entry_price"],
                plan["initial_stop_loss"],
                plan["initial_tp1"],
                plan["initial_tp2"],
                plan["initial_setup"],
                plan["initial_decision"],
                plan["initial_score"],
                timestamp,
            ),
        )
    elif needs_repair or (not existing_signal_id and new_signal_id):
        conn.execute(
            """
            UPDATE position_initial_plan
            SET linked_signal_id=?, machine_entry_price=?, initial_stop_loss=?,
                initial_tp1=?, initial_tp2=?, initial_setup=?, initial_decision=?,
                initial_score=?
            WHERE position_id=?
            """,
            (
                plan["linked_signal_id"],
                plan["machine_entry_price"],
                plan["initial_stop_loss"],
                plan["initial_tp1"],
                plan["initial_tp2"],
                plan["initial_setup"],
                plan["initial_decision"],
                plan["initial_score"],
                position_id,
            ),
        )

    if position_signal_id != new_signal_id and (needs_repair or new_signal_id or not position_signal_id):
        conn.execute(
            "UPDATE portfolio_positions SET signal_id=?, updated_at=? WHERE position_id=?",
            (new_signal_id or None, timestamp, position_id),
        )

    if needs_repair:
        reason = "INVALID_SIGNAL_LINK_AS_OF_BUY_DATE"
        if current_signal is not None:
            signal_date = _date_text(current_signal["signal_date"] if "signal_date" in current_signal.keys() else "")
            terminal_date = _signal_terminal_date(current_signal)
            if signal_date and signal_date > buy_date:
                reason = "FUTURE_SIGNAL_LINK_REJECTED"
            elif terminal_date and terminal_date < buy_date:
                reason = "PRE_BUY_TERMINAL_SIGNAL_REJECTED"
        _log_link_repair(
            conn,
            position_id=position_id,
            symbol=symbol,
            buy_date=buy_date,
            previous_signal_id=current_signal_id,
            new_signal_id=new_signal_id,
            reason=reason,
        )

    conn.commit()
    row = conn.execute(
        "SELECT * FROM position_initial_plan WHERE position_id=?",
        (position_id,),
    ).fetchone()
    return dict(row) if row else None


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
    """Repair/freeze temporal linkage, then fill only missing manual fields."""
    conn.executescript(SCHEMA_SQL)
    freeze_position_initial_plan(conn, position_id)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain/freeze initial plan for actual OPEN portfolio")
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

    freeze = sub.add_parser("freeze")
    freeze.add_argument("--position-id", default="")
    freeze.add_argument("--symbol", default="")
    freeze.add_argument("--buy-date", default="")
    freeze.add_argument("--quantity", type=float, default=None)
    freeze.add_argument("--buy-price", type=float, default=None)

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
        if args.command == "freeze":
            row = resolve_open_position(
                conn,
                position_id=args.position_id,
                symbol=args.symbol,
                buy_date=args.buy_date,
                quantity=args.quantity,
                buy_price=args.buy_price,
            )
            plan = freeze_position_initial_plan(conn, str(row["position_id"]))
            linked = str((plan or {}).get("linked_signal_id") or "UNLINKED")
            setup = str((plan or {}).get("initial_setup") or "MANUAL/UNSET")
            print(
                f"Initial plan dibekukan: position_id={row['position_id']} "
                f"signal={linked} setup={setup} buy_date={row['buy_date']}"
            )
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
