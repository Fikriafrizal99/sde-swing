from __future__ import annotations

"""Narrow integrity guards for actual portfolio maintenance.

This overlay intentionally leaves scoring, decision rules, lifecycle evaluation,
and reporting untouched.  It only hardens four portfolio-facing invariants:

* a WAITING setup upgraded to BUY CONFIRMED keeps setup + executable plan coherent;
* actual BUY signal resolution is historical/as-of ``buy_date``;
* explicit BUY signal IDs must also be valid as-of ``buy_date``;
* SELL by symbol fails closed when more than one OPEN lot exists.

The functions are installed onto ``outcome_tracker_baseline`` from
``modules.analytics.__init__`` so every normal facade/CLI path uses the same
rules without forking the baseline implementation.
"""

import hashlib
import sqlite3
from datetime import datetime
from typing import Any

from swing_utils import normalize_symbol
from modules.analytics import outcome_tracker_baseline as _baseline

_ORIGINAL_UPSERT_SIGNAL = _baseline.upsert_signal
_INSTALLED = False


def _date_text(value: Any) -> str:
    return _baseline.parse_date(value)


def _signal_terminal_date(signal: sqlite3.Row) -> str:
    exit_date = _date_text(signal["exit_date"] if "exit_date" in signal.keys() else "")
    if exit_date:
        return exit_date
    status = _baseline.norm_text(
        signal["current_status"] if "current_status" in signal.keys() else ""
    ).upper()
    if status in _baseline.TERMINAL_STATUSES:
        return _date_text(signal["signal_date"] if "signal_date" in signal.keys() else "")
    return ""


def _signal_valid_as_of_buy(signal: sqlite3.Row | None, symbol: str, buy_date: str) -> bool:
    if signal is None:
        return False
    if normalize_symbol(signal["symbol"] if "symbol" in signal.keys() else "") != normalize_symbol(symbol):
        return False
    signal_date = _date_text(signal["signal_date"] if "signal_date" in signal.keys() else "")
    if not signal_date or signal_date > buy_date:
        return False
    status = _baseline.norm_text(
        signal["current_status"] if "current_status" in signal.keys() else ""
    ).upper()
    if status == "INVALID_DATA":
        return False
    terminal_date = _signal_terminal_date(signal)
    if terminal_date and terminal_date < buy_date:
        return False
    if status in {"EXPIRED", "INVALIDATED_BEFORE_ENTRY"} and terminal_date == buy_date:
        return False
    return True


def _resolve_signal_id_as_of_buy(
    conn: sqlite3.Connection,
    symbol: str,
    buy_date: str,
    signal_id: str = "",
) -> str:
    normalized = normalize_symbol(symbol)
    explicit = _baseline.norm_text(signal_id)
    if explicit:
        row = conn.execute(
            "SELECT * FROM signal_outcome_ledger WHERE signal_id=?",
            (explicit,),
        ).fetchone()
        if row is None:
            raise ValueError(f"SIGNAL_ID_NOT_FOUND:{explicit}")
        if not _signal_valid_as_of_buy(row, normalized, buy_date):
            raise ValueError(f"SIGNAL_ID_NOT_VALID_AS_OF_BUY_DATE:{explicit}")
        return explicit

    rows = conn.execute(
        """
        SELECT * FROM signal_outcome_ledger
        WHERE symbol=? AND signal_date<=?
        ORDER BY signal_date DESC, updated_at DESC, signal_id DESC
        """,
        (normalized, buy_date),
    ).fetchall()
    for row in rows:
        if _signal_valid_as_of_buy(row, normalized, buy_date):
            return str(row["signal_id"])
    return ""


def upsert_signal(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    """Keep setup label coherent with the confirmed WAITING plan bundle."""
    result = _ORIGINAL_UPSERT_SIGNAL(conn, record)
    if (
        result == "UPDATED_ACTIVE"
        and _baseline.norm_text(record.get("signal_type")).upper() == "BUY CONFIRMED"
        and _baseline.norm_text(record.get("current_status")).upper() != "INVALIDATED_BEFORE_ENTRY"
    ):
        active = conn.execute(
            """
            SELECT signal_id FROM signal_outcome_ledger
            WHERE symbol=? AND current_status='WAITING_TRIGGER'
            ORDER BY signal_date ASC LIMIT 1
            """,
            (normalize_symbol(record.get("symbol")),),
        ).fetchone()
        if active:
            # The baseline already updates trigger/entry/SL/TP/source_json as one
            # confirmed WAITING-plan bundle.  setup_type is the missing member.
            conn.execute(
                "UPDATE signal_outcome_ledger SET setup_type=? WHERE signal_id=?",
                (record.get("setup_type"), active["signal_id"]),
            )
    return result


def record_portfolio_buy(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    quantity: float,
    buy_price: float,
    buy_date: str = "",
    signal_id: str = "",
    notes: str = "",
    source_run_id: str = "",
) -> str:
    """Record actual BUY using only signal evidence valid on the buy date."""
    symbol = normalize_symbol(symbol)
    quantity = float(quantity)
    buy_price = float(buy_price)
    if not symbol:
        raise ValueError("SYMBOL_REQUIRED")
    if quantity <= 0 or buy_price <= 0:
        raise ValueError("QUANTITY_AND_PRICE_MUST_BE_POSITIVE")
    buy_date = _baseline.parse_date(buy_date) or datetime.now().astimezone().date().isoformat()
    linked_signal = _resolve_signal_id_as_of_buy(conn, symbol, buy_date, signal_id)
    position_id = hashlib.sha256(
        "|".join([
            linked_signal,
            symbol,
            buy_date,
            f"{quantity:.8f}",
            f"{buy_price:.8f}",
        ]).encode("utf-8")
    ).hexdigest()[:24]
    timestamp = _baseline.now_text()
    conn.execute(
        """
        INSERT OR IGNORE INTO portfolio_positions (
            position_id, signal_id, symbol, buy_date, quantity, buy_price,
            current_status, sell_date, sell_price, realized_return_pct, notes,
            source_run_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', NULL, NULL, NULL, ?, ?, ?, ?)
        """,
        (
            position_id,
            linked_signal,
            symbol,
            buy_date,
            quantity,
            buy_price,
            _baseline.norm_text(notes),
            _baseline.norm_text(source_run_id),
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return position_id


def record_portfolio_sell(
    conn: sqlite3.Connection,
    *,
    position_id: str = "",
    symbol: str = "",
    sell_price: float,
    sell_date: str = "",
) -> str:
    """Fail closed instead of guessing a lot when symbol has multiple OPEN positions."""
    sell_price = float(sell_price)
    if sell_price <= 0:
        raise ValueError("SELL_PRICE_MUST_BE_POSITIVE")
    sell_date = _baseline.parse_date(sell_date) or datetime.now().astimezone().date().isoformat()
    if _baseline.norm_text(position_id):
        row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE position_id=? AND current_status='OPEN'",
            (_baseline.norm_text(position_id),),
        ).fetchone()
    else:
        normalized = normalize_symbol(symbol)
        rows = conn.execute(
            """
            SELECT * FROM portfolio_positions
            WHERE symbol=? AND current_status='OPEN'
            ORDER BY buy_date DESC, created_at DESC, position_id DESC
            """,
            (normalized,),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("MULTIPLE_OPEN_POSITIONS_USE_POSITION_ID")
        row = rows[0] if rows else None
    if not row:
        raise ValueError("OPEN_PORTFOLIO_POSITION_NOT_FOUND")
    realized = _baseline.ret_pct(sell_price, _baseline.as_float(row["buy_price"]))
    conn.execute(
        """
        UPDATE portfolio_positions
        SET current_status='CLOSED', sell_date=?, sell_price=?, realized_return_pct=?, updated_at=?
        WHERE position_id=?
        """,
        (sell_date, sell_price, realized, _baseline.now_text(), row["position_id"]),
    )
    conn.commit()
    return str(row["position_id"])


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _baseline.upsert_signal = upsert_signal
    _baseline.record_portfolio_buy = record_portfolio_buy
    _baseline.record_portfolio_sell = record_portfolio_sell
    _INSTALLED = True
