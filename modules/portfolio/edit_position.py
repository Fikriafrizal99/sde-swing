#!/usr/bin/env python3
from __future__ import annotations

"""Safe editor for actual OPEN portfolio positions.

Only transaction facts are editable here: quantity, actual buy price, buy date,
and notes. TP/SL remain owned by manual_position_plan.py / machine initial plan.
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

DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"

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


def resolve_open_position(conn: sqlite3.Connection, *, position_id: str = "", symbol: str = "") -> sqlite3.Row:
    if not table_exists(conn, "portfolio_positions"):
        raise ValueError("PORTFOLIO_TABLE_NOT_FOUND")
    if position_id.strip():
        row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE position_id=? AND UPPER(current_status)='OPEN'",
            (position_id.strip(),),
        ).fetchone()
        if not row:
            raise ValueError("OPEN_PORTFOLIO_POSITION_NOT_FOUND")
        return row

    normalized = normalize_symbol(symbol)
    if not normalized:
        raise ValueError("POSITION_ID_OR_SYMBOL_REQUIRED")
    rows = conn.execute(
        "SELECT * FROM portfolio_positions WHERE UPPER(symbol)=? AND UPPER(current_status)='OPEN' ORDER BY created_at DESC",
        (normalized,),
    ).fetchall()
    if not rows:
        raise ValueError("OPEN_PORTFOLIO_POSITION_NOT_FOUND")
    if len(rows) > 1:
        raise ValueError("MULTIPLE_OPEN_POSITIONS_USE_POSITION_ID")
    return rows[0]


def validate_buy_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value[:10])
    except Exception as exc:
        raise ValueError("BUY_DATE_MUST_BE_YYYY_MM_DD") from exc
    if parsed > date.today():
        raise ValueError("BUY_DATE_CANNOT_BE_FUTURE")
    return parsed.isoformat()


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
    row = resolve_open_position(conn, position_id=position_id, symbol=symbol)
    before = dict(row)

    new_quantity = float(quantity) if quantity is not None else float(row["quantity"])
    new_buy_price = float(buy_price) if buy_price is not None else float(row["buy_price"])
    new_buy_date = validate_buy_date(buy_date) if buy_date else str(row["buy_date"])
    new_notes = notes if notes is not None else row["notes"]

    if new_quantity <= 0:
        raise ValueError("QUANTITY_MUST_BE_POSITIVE")
    if new_buy_price <= 0:
        raise ValueError("BUY_PRICE_MUST_BE_POSITIVE")

    changed = (
        abs(new_quantity - float(row["quantity"])) > 1e-12
        or abs(new_buy_price - float(row["buy_price"])) > 1e-12
        or new_buy_date != str(row["buy_date"])
        or new_notes != row["notes"]
    )
    if not changed:
        return before, ["Tidak ada perubahan data."]

    timestamp = now_text()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE portfolio_positions
            SET quantity=?, buy_price=?, buy_date=?, notes=?, updated_at=?
            WHERE position_id=? AND UPPER(current_status)='OPEN'
            """,
            (new_quantity, new_buy_price, new_buy_date, new_notes, timestamp, row["position_id"]),
        )

        # Position Management caches the actual buy facts in position_initial_plan.
        # Update only those facts; machine Entry/SL/TP/setup/score remain untouched.
        if table_exists(conn, "position_initial_plan"):
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

    warnings = _plan_warning(conn, str(row["position_id"]), new_buy_price)
    return after, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Edit actual OPEN portfolio position safely")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--position-id", default="")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--quantity", type=float, default=None)
    parser.add_argument("--buy-price", type=float, default=None)
    parser.add_argument("--buy-date", default=None)
    parser.add_argument("--notes", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.quantity is None and args.buy_price is None and args.buy_date is None and args.notes is None:
        print("[SKIPPED] Tidak ada field yang diminta untuk diubah.")
        return 0
    conn = connect(Path(args.db))
    try:
        try:
            updated, warnings = edit_open_position(
                conn,
                position_id=args.position_id,
                symbol=args.symbol,
                quantity=args.quantity,
                buy_price=args.buy_price,
                buy_date=args.buy_date,
                notes=args.notes,
            )
        except ValueError as exc:
            print(f"[FAILED] {exc}")
            return 2
        print(
            "[OK] Posisi OPEN diperbarui: "
            f"{normalize_symbol(updated.get('symbol'))} | position_id={updated.get('position_id')} | "
            f"qty={updated.get('quantity')} | buy={updated.get('buy_price')} | date={updated.get('buy_date')}"
        )
        for warning in warnings:
            print(f"[WARNING] {warning}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
