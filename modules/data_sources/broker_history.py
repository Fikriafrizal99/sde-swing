from __future__ import annotations

"""Broker multi-day history storage.

Stores daily BrokerFlow records keyed by (symbol, market_date, broker_code,
source) with upsert semantics.  Retention is enforced at 60 trading sessions
minimum, 120 target.  Cumulative broker files are never used as daily data.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Generator, Sequence

from swing_utils import ensure_dir

DEFAULT_DB = Path("data/database/broker_multiday.db")
RETENTION_MIN = 60
RETENTION_TARGET = 120


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    ensure_dir(db_path.parent)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection, None, None]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS broker_daily (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT    NOT NULL,
            market_date     TEXT    NOT NULL,
            broker_code     TEXT    NOT NULL,
            broker_type     TEXT    NOT NULL DEFAULT 'UNKNOWN',
            side            TEXT    NOT NULL,
            source          TEXT    NOT NULL DEFAULT 'STOCKBIT',
            net_value       REAL,
            net_lot         REAL,
            gross_value     REAL,
            gross_lot       REAL,
            frequency       REAL,
            avg_price       REAL,
            rank            REAL,
            quality_status  TEXT    NOT NULL DEFAULT 'UNVALIDATED',
            received_at     TEXT    NOT NULL,
            UNIQUE (symbol, market_date, broker_code, side, source)
        );
        CREATE INDEX IF NOT EXISTS idx_bd_symbol_date
            ON broker_daily (symbol, market_date);
        CREATE INDEX IF NOT EXISTS idx_bd_date
            ON broker_daily (market_date);

        CREATE TABLE IF NOT EXISTS trading_sessions (
            market_date TEXT PRIMARY KEY
        );
    """)
    conn.commit()


def upsert_broker_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO broker_daily
            (symbol, market_date, broker_code, broker_type, side, source,
             net_value, net_lot, gross_value, gross_lot, frequency, avg_price,
             rank, quality_status, received_at)
        VALUES
            (:symbol, :market_date, :broker_code, :broker_type, :side, :source,
             :net_value, :net_lot, :gross_value, :gross_lot, :frequency, :avg_price,
             :rank, :quality_status, :received_at)
        ON CONFLICT (symbol, market_date, broker_code, side, source)
        DO UPDATE SET
            broker_type    = excluded.broker_type,
            net_value      = excluded.net_value,
            net_lot        = excluded.net_lot,
            gross_value    = excluded.gross_value,
            gross_lot      = excluded.gross_lot,
            frequency      = excluded.frequency,
            avg_price      = excluded.avg_price,
            rank           = excluded.rank,
            quality_status = excluded.quality_status,
            received_at    = excluded.received_at
    """
    with transaction(conn):
        conn.executemany(sql, rows)
    return len(rows)


def register_trading_sessions(conn: sqlite3.Connection, dates: Sequence[str]) -> None:
    with transaction(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO trading_sessions (market_date) VALUES (?)",
            [(d,) for d in dates],
        )


def get_trading_sessions_before(
    conn: sqlite3.Connection, up_to: str, limit: int = RETENTION_TARGET
) -> list[str]:
    rows = conn.execute(
        "SELECT market_date FROM trading_sessions WHERE market_date <= ? "
        "ORDER BY market_date DESC LIMIT ?",
        (up_to, limit),
    ).fetchall()
    return sorted(r["market_date"] for r in rows)


def load_broker_window(
    conn: sqlite3.Connection,
    symbol: str,
    sessions: list[str],
) -> list[dict[str, Any]]:
    if not sessions:
        return []
    placeholders = ",".join("?" * len(sessions))
    rows = conn.execute(
        f"SELECT * FROM broker_daily WHERE symbol=? AND market_date IN ({placeholders}) "
        f"ORDER BY market_date, side, rank",
        [symbol, *sessions],
    ).fetchall()
    return [dict(r) for r in rows]


def enforce_retention(
    conn: sqlite3.Connection,
    keep_sessions: int = RETENTION_TARGET,
) -> int:
    """Delete broker_daily rows older than the most recent keep_sessions sessions."""
    cutoff_rows = conn.execute(
        "SELECT market_date FROM trading_sessions ORDER BY market_date DESC LIMIT 1 OFFSET ?",
        (keep_sessions,),
    ).fetchone()
    if cutoff_rows is None:
        return 0
    cutoff = cutoff_rows["market_date"]
    with transaction(conn):
        cur = conn.execute("DELETE FROM broker_daily WHERE market_date < ?", (cutoff,))
        conn.execute("DELETE FROM trading_sessions WHERE market_date < ?", (cutoff,))
    return cur.rowcount