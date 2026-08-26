#!/usr/bin/env python3
from __future__ import annotations

"""Safe editor for actual OPEN or CLOSED portfolio positions.

Transaction facts can be corrected without rewriting machine signal history.
OPEN positions allow quantity/buy-price/buy-date/notes edits. CLOSED positions
add sell-price/sell-date corrections and recalculate actual realized return.

When the actual buy date changes, the cached initial plan is rebuilt from
portfolio evidence valid as-of the corrected buy date. A manual plan remains
manual; otherwise the latest eligible historical signal is selected. Future or
otherwise invalid same-symbol signals are never borrowed.
"""

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import normalize_symbol
from modules.portfolio.manual_position_plan import apply_manual_plan_to_initial_plan

DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
VALID_STATUSES = {"OPEN", "CLOSED"}

AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_edit_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    edited_at TEXT NOT NULL,
    previous_json TEXT NOT NULL,
    new_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_edit_audit_position
    ON portfolio_edit_audit(position_id, edited_at);
"""


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(AUDIT_SQL)
    conn.commit()
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _status_text(value: str) -> str:
    status = str(value or "OPEN").strip().upper()
    if status not in VALID_STATUSES:
        raise ValueError("STATUS_MUST_BE_OPEN_OR_CLOSED")
    return status


def resolve_position(
    conn: sqlite3.Connection,
    *,
    position_id: str = "",
    symbol: str = "",
    status: str = "OPEN",
) -> sqlite3.Row:
    status = _status_text(status)
    if not table_exists(conn, "portfolio_positions"):
        raise ValueError("PORTFOLIO_TABLE_NOT_FOUND")

    if position_id.strip():
        row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE position_id=? AND UPPER(current_status)=?",
            (position_id.strip(), status),
        ).fetchone()
        if not row:
            raise ValueError(f"{status}_PORTFOLIO_POSITION_NOT_FOUND")
        return row

    normalized = normalize_symbol(symbol)
    if not normalized:
        raise ValueError("POSITION_ID_OR_SYMBOL_REQUIRED")
    rows = conn.execute(
        """
        SELECT * FROM portfolio_positions
        WHERE UPPER(symbol)=? AND UPPER(current_status)=?
        ORDER BY COALESCE(sell_date, buy_date) DESC, created_at DESC
        """,
        (normalized, status),
    ).fetchall()
    if not rows:
        raise ValueError(f"{status}_PORTFOLIO_POSITION_NOT_FOUND")
    if len(rows) > 1:
        raise ValueError(f"MULTIPLE_{status}_POSITIONS_USE_POSITION_ID")
    return rows[0]


def resolve_open_position(conn: sqlite3.Connection, *, position_id: str = "", symbol: str = "") -> sqlite3.Row:
    """Backward-compatible OPEN-only resolver."""
    return resolve_position(conn, position_id=position_id, symbol=symbol, status="OPEN")


def validate_date(value: str, field: str) -> str:
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except Exception as exc:
        raise ValueError(f"{field.upper()}_MUST_BE_YYYY_MM_DD") from exc
    if parsed > date.today():
        raise ValueError(f"{field.upper()}_CANNOT_BE_FUTURE")
    return parsed.isoformat()


def validate_buy_date(value: str) -> str:
    return validate_date(value, "BUY_DATE")


def _plan_warning(conn: sqlite3.Connection, position_id: str, buy_price: float) -> list[str]:
    if not table_exists(conn, "position_initial_plan"):
        return []
    row = conn.execute(
        "SELECT initial_stop_loss, initial_tp1, initial_tp2 FROM position_initial_plan WHERE position_id=?",
        (position_id,),
    ).fetchone()
    if not row:
        return []
    warnings: list[str] = []
    sl = row["initial_stop_loss"]
    tp1 = row["initial_tp1"]
    tp2 = row["initial_tp2"]
    if sl is not None and float(sl) >= buy_price:
        warnings.append("Initial SL berada di/atas harga beli baru; cek ulang menu TP/SL.")
    if tp1 is not None and float(tp1) <= buy_price:
        warnings.append("TP1 berada di/bawah harga beli baru; cek ulang initial plan.")
    if tp2 is not None and float(tp2) <= buy_price:
        warnings.append("TP2 berada di/bawah harga beli baru; cek ulang initial plan.")
    return warnings


def _realized_return_pct(buy_price: float, sell_price: float) -> float:
    return (sell_price / buy_price - 1.0) * 100.0


def _rebuild_initial_plan_after_buy_date_change(
    conn: sqlite3.Connection,
    *,
    position_id: str,
) -> list[str]:
    """Re-resolve cached initial plan against the corrected actual buy date."""
    warnings: list[str] = []
    try:
        # Deleting only the derived snapshot is intentional. The machine signal
        # ledger and optional manual plan remain immutable/auditable sources.
        if table_exists(conn, "position_initial_plan"):
            conn.execute("DELETE FROM position_initial_plan WHERE position_id=?", (position_id,))
            conn.commit()
        plan = apply_manual_plan_to_initial_plan(conn, position_id)
        if not plan:
            warnings.append("Initial plan belum dapat dibangun ulang setelah koreksi tanggal BUY.")
        elif not str(plan.get("linked_signal_id") or "") and not str(plan.get("initial_setup") or ""):
            warnings.append(
                "Tidak ada signal historis/manual plan yang valid pada tanggal BUY baru; linkage dibiarkan kosong."
            )
    except Exception as exc:
        warnings.append(f"Rebuild initial plan gagal: {type(exc).__name__}: {exc}")
    return warnings


def edit_position(
    conn: sqlite3.Connection,
    *,
    position_id: str = "",
    symbol: str = "",
    status: str = "OPEN",
    quantity: float | None = None,
    buy_price: float | None = None,
    buy_date: str | None = None,
    sell_price: float | None = None,
    sell_date: str | None = None,
    notes: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    status = _status_text(status)
    row = resolve_position(conn, position_id=position_id, symbol=symbol, status=status)
    before = dict(row)

    new_quantity = float(quantity) if quantity is not None else float(row["quantity"])
    new_buy_price = float(buy_price) if buy_price is not None else float(row["buy_price"])
    new_buy_date = validate_buy_date(buy_date) if buy_date else str(row["buy_date"])
    new_notes = notes if notes is not None else row["notes"]

    if new_quantity <= 0:
        raise ValueError("QUANTITY_MUST_BE_POSITIVE")
    if new_buy_price <= 0:
        raise ValueError("BUY_PRICE_MUST_BE_POSITIVE")

    if status == "OPEN":
        if sell_price is not None or sell_date is not None:
            raise ValueError("OPEN_POSITION_SELL_FACTS_MUST_USE_RECORD_SELL")
        new_sell_price = row["sell_price"]
        new_sell_date = row["sell_date"]
        realized_return = row["realized_return_pct"]
    else:
        raw_sell_price = sell_price if sell_price is not None else row["sell_price"]
        raw_sell_date = sell_date if sell_date is not None else row["sell_date"]
        if raw_sell_price is None:
            raise ValueError("CLOSED_POSITION_SELL_PRICE_REQUIRED")
        new_sell_price = float(raw_sell_price)
        if new_sell_price <= 0:
            raise ValueError("SELL_PRICE_MUST_BE_POSITIVE")
        if not raw_sell_date:
            raise ValueError("CLOSED_POSITION_SELL_DATE_REQUIRED")
        new_sell_date = validate_date(str(raw_sell_date), "SELL_DATE")
        if new_sell_date < new_buy_date:
            raise ValueError("SELL_DATE_CANNOT_BE_BEFORE_BUY_DATE")
        realized_return = _realized_return_pct(new_buy_price, new_sell_price)

    changed = (
        abs(new_quantity - float(row["quantity"])) > 1e-12
        or abs(new_buy_price - float(row["buy_price"])) > 1e-12
        or new_buy_date != str(row["buy_date"])
        or new_notes != row["notes"]
        or (status == "CLOSED" and (
            float(new_sell_price) != float(row["sell_price"])
            or str(new_sell_date) != str(row["sell_date"])
            or row["realized_return_pct"] is None
            or abs(float(realized_return) - float(row["realized_return_pct"] or 0.0)) > 1e-12
        ))
    )
    if not changed:
        return before, ["Tidak ada perubahan data."]

    timestamp = now_text()
    buy_date_changed = new_buy_date != str(row["buy_date"])
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE portfolio_positions
            SET quantity=?, buy_price=?, buy_date=?, sell_price=?, sell_date=?,
                realized_return_pct=?, notes=?, updated_at=?
            WHERE position_id=? AND UPPER(current_status)=?
            """,
            (
                new_quantity,
                new_buy_price,
                new_buy_date,
                new_sell_price,
                new_sell_date,
                realized_return,
                new_notes,
                timestamp,
                row["position_id"],
                status,
            ),
        )

        # The initial-plan snapshot stores actual buy facts. A corrected buy
        # date must not retain a previously frozen machine linkage; it will be
        # rebuilt after this transaction from evidence valid as-of the new date.
        if table_exists(conn, "position_initial_plan") and not buy_date_changed:
            conn.execute(
                "UPDATE position_initial_plan SET buy_price=?, buy_date=? WHERE position_id=?",
                (new_buy_price, new_buy_date, row["position_id"]),
            )
        if table_exists(conn, "portfolio_manual_initial_plan"):
            conn.execute(
                "UPDATE portfolio_manual_initial_plan SET buy_date=?, updated_at=? WHERE position_id=?",
                (new_buy_date, timestamp, row["position_id"]),
            )

        after_row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE position_id=?", (row["position_id"],)
        ).fetchone()
        after = dict(after_row) if after_row else {}
        conn.execute(
            """
            INSERT INTO portfolio_edit_audit(position_id, symbol, edited_at, previous_json, new_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                row["position_id"],
                normalize_symbol(row["symbol"]),
                timestamp,
                json.dumps(before, ensure_ascii=False, default=str),
                json.dumps(after, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    warnings: list[str] = []
    if buy_date_changed:
        warnings.extend(
            _rebuild_initial_plan_after_buy_date_change(
                conn,
                position_id=str(row["position_id"]),
            )
        )
    warnings.extend(_plan_warning(conn, str(row["position_id"]), new_buy_price))
    if status == "CLOSED":
        warnings.append(
            "Koreksi CLOSED mengubah fakta transaksi aktual dan realized return; snapshot report Position Management historis tetap disimpan sebagai audit saat itu."
        )
    return after, warnings


def edit_open_position(
    conn: sqlite3.Connection,
    *,
    position_id: str = "",
    symbol: str = "",
    quantity: float | None = None,
    buy_price: float | None = None,
    buy_date: str | None = None,
    notes: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Backward-compatible OPEN editor."""
    return edit_position(
        conn,
        position_id=position_id,
        symbol=symbol,
        status="OPEN",
        quantity=quantity,
        buy_price=buy_price,
        buy_date=buy_date,
        notes=notes,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Edit actual OPEN/CLOSED portfolio position safely")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--position-id", default="")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--status", choices=("OPEN", "CLOSED", "open", "closed"), default="OPEN")
    parser.add_argument("--quantity", type=float, default=None)
    parser.add_argument("--buy-price", type=float, default=None)
    parser.add_argument("--buy-date", default=None)
    parser.add_argument("--sell-price", type=float, default=None)
    parser.add_argument("--sell-date", default=None)
    parser.add_argument("--notes", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (
        args.quantity is None
        and args.buy_price is None
        and args.buy_date is None
        and args.sell_price is None
        and args.sell_date is None
        and args.notes is None
    ):
        print("[SKIPPED] Tidak ada field yang diminta untuk diubah.")
        return 0
    conn = connect(Path(args.db))
    try:
        try:
            updated, warnings = edit_position(
                conn,
                position_id=args.position_id,
                symbol=args.symbol,
                status=args.status,
                quantity=args.quantity,
                buy_price=args.buy_price,
                buy_date=args.buy_date,
                sell_price=args.sell_price,
                sell_date=args.sell_date,
                notes=args.notes,
            )
        except ValueError as exc:
            print(f"[FAILED] {exc}")
            return 2
        status = str(updated.get("current_status") or args.status).upper()
        detail = (
            f"{normalize_symbol(updated.get('symbol'))} | position_id={updated.get('position_id')} | "
            f"qty={updated.get('quantity')} | buy={updated.get('buy_price')} | date={updated.get('buy_date')}"
        )
        if status == "CLOSED":
            detail += (
                f" | sell={updated.get('sell_price')} | sell_date={updated.get('sell_date')} "
                f"| realized={float(updated.get('realized_return_pct') or 0.0):.2f}%"
            )
        print(f"[OK] Posisi {status} diperbarui: {detail}")
        for warning in warnings:
            print(f"[WARNING] {warning}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
