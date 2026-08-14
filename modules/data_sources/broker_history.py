from __future__ import annotations

"""Broker multi-day history storage.

Stores daily BrokerFlow records keyed by (symbol, market_date, broker_code,
source).  The historical public function is still named ``upsert`` for
backward compatibility, but the canonical daily row is now immutable: an
identical capture is a no-op and a changed capture is recorded as a revision
before the import is rejected.  Retention is enforced at 60 trading sessions
minimum, 120 target.  Cumulative broker files are never used as daily data.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Sequence

import pandas as pd

from swing_utils import ensure_dir, file_sha256

DEFAULT_DB = Path("data/database/broker_multiday.db")
RETENTION_MIN = 60
RETENTION_TARGET = 120


def read_daily_capture_rows(
    path: Path,
    *,
    source: str = "STOCKBIT_1D",
    capture_id: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize one real 1D raw export without ever splitting an aggregate.

    The raw parser is shared with the Broker Fusion presentation path.  A file
    is eligible only when every usable row has the same FROM_DATE and
    TO_DATE, and that date is one real session.  Multi-day/aggregate files
    therefore return no rows rather than being decomposed into fake days.
    """
    from modules.broker_bridge.broker_raw import read_normalized_broker_raw

    frame = read_normalized_broker_raw(Path(path))
    if frame.empty or "FROM_DATE" not in frame.columns or "TO_DATE" not in frame.columns:
        return [], []
    from_values = pd.to_datetime(frame["FROM_DATE"], errors="coerce")
    to_values = pd.to_datetime(frame["TO_DATE"], errors="coerce")
    valid = from_values.notna() & to_values.notna()
    if not bool(valid.any()):
        return [], []
    same_session = from_values[valid].dt.date == to_values[valid].dt.date
    if not bool(same_session.all()):
        return [], []
    dates = sorted({value.isoformat() for value in to_values[valid].dt.date})
    if len(dates) != 1:
        return [], dates
    market_date = dates[0]
    capture = str(capture_id or file_sha256(Path(path)))
    rows: list[dict[str, Any]] = []
    for raw in frame.loc[valid].to_dict(orient="records"):
        symbol = str(raw.get("SYMBOL", "")).strip().upper()
        broker_code = str(raw.get("BROKER_CODE", "")).strip().upper()
        side = str(raw.get("SIDE", "")).strip().upper()
        if not symbol or not broker_code or side not in {"BUY", "SELL"}:
            continue
        def number(key: str) -> float | None:
            value = raw.get(key)
            try:
                return float(value) if value not in (None, "") and not pd.isna(value) else None
            except (TypeError, ValueError):
                return None

        rows.append({
            "symbol": symbol,
            "market_date": market_date,
            "broker_code": broker_code,
            "broker_type": str(raw.get("BROKER_TYPE") or "UNKNOWN").strip().upper(),
            "side": side,
            "net_value": number("NET_VALUE"),
            "net_lot": number("NET_LOT"),
            "gross_value": number("GROSS_VALUE"),
            "gross_lot": number("GROSS_LOT"),
            "frequency": number("FREQUENCY"),
            "avg_price": number("AVG_PRICE"),
            "rank": number("RANK"),
            "source": str(source or "STOCKBIT_1D").upper(),
            "quality_status": "VALIDATED",
            "received_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "capture_id": capture,
        })
    return rows, [market_date] if rows else []


def ingest_daily_capture_files(
    conn: sqlite3.Connection,
    paths: Sequence[Path],
    *,
    as_of_date: str = "",
    source: str = "STOCKBIT_1D",
) -> dict[str, Any]:
    """Ingest only immutable real-1D captures and report exact coverage."""
    accepted_files: list[str] = []
    accepted_dates: set[str] = set()
    rows_seen = 0
    for path in paths:
        rows, dates = read_daily_capture_rows(path, source=source)
        if not rows or len(dates) != 1:
            continue
        market_date = dates[0]
        if as_of_date and market_date > str(as_of_date)[:10]:
            continue
        upsert_broker_rows(conn, rows)
        register_trading_sessions(conn, [market_date])
        accepted_files.append(str(Path(path).resolve()))
        accepted_dates.add(market_date)
        rows_seen += len(rows)
    return {
        "accepted_files": accepted_files,
        "market_dates": sorted(accepted_dates),
        "rows": rows_seen,
        "source": str(source or "STOCKBIT_1D").upper(),
    }


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
            capture_id      TEXT,
            capture_hash    TEXT,
            UNIQUE (symbol, market_date, broker_code, side, source)
        );
        CREATE INDEX IF NOT EXISTS idx_bd_symbol_date
            ON broker_daily (symbol, market_date);
        CREATE INDEX IF NOT EXISTS idx_bd_date
            ON broker_daily (market_date);

        CREATE TABLE IF NOT EXISTS trading_sessions (
            market_date TEXT PRIMARY KEY
        );

        -- Additive lineage table.  It deliberately does not replace or
        -- rewrite broker_daily: the first accepted observation remains the
        -- canonical value used by the rolling engine.
        CREATE TABLE IF NOT EXISTS broker_daily_revisions (
            revision_id             TEXT PRIMARY KEY,
            symbol                  TEXT NOT NULL,
            market_date             TEXT NOT NULL,
            broker_code             TEXT NOT NULL,
            side                    TEXT NOT NULL,
            source                  TEXT NOT NULL,
            capture_id              TEXT,
            capture_hash            TEXT NOT NULL,
            revision_type           TEXT NOT NULL,
            existing_capture_hash  TEXT,
            row_json                TEXT NOT NULL,
            received_at             TEXT,
            created_at              TEXT NOT NULL,
            UNIQUE (symbol, market_date, broker_code, side, source, capture_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_bdr_identity
            ON broker_daily_revisions (symbol, market_date, broker_code, side, source);
    """)
    # Existing installations may already have broker_daily without the two
    # optional lineage columns.  Keep the migration additive and idempotent.
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(broker_daily)").fetchall()}
    if "capture_id" not in columns:
        conn.execute("ALTER TABLE broker_daily ADD COLUMN capture_id TEXT")
    if "capture_hash" not in columns:
        conn.execute("ALTER TABLE broker_daily ADD COLUMN capture_hash TEXT")
    conn.commit()


def _normalize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.15g}"
    return str(value).strip()


_BROKER_VALUE_COLUMNS = (
    "broker_type",
    "net_value",
    "net_lot",
    "gross_value",
    "gross_lot",
    "frequency",
    "avg_price",
    "rank",
    "quality_status",
)


def _prepared_row(row: dict[str, Any]) -> dict[str, Any]:
    """Apply schema defaults without changing the caller's mapping."""
    prepared = dict(row)
    prepared.setdefault("broker_type", "UNKNOWN")
    prepared.setdefault("source", "STOCKBIT")
    prepared.setdefault("quality_status", "UNVALIDATED")
    prepared.setdefault("received_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    prepared.setdefault("capture_id", "")
    prepared.setdefault("capture_hash", "")
    return prepared


def _capture_hash(row: dict[str, Any]) -> str:
    payload = {
        key: row.get(key)
        for key in (
            "symbol", "market_date", "broker_code", "broker_type", "side", "source",
            *_BROKER_VALUE_COLUMNS[1:],
        )
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row_payload(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)


def _broker_row_conflicts(existing: sqlite3.Row, incoming: dict[str, Any]) -> bool:
    # ``received_at`` is capture metadata, not an observation value.  A
    # repeated browser download with a new ingestion timestamp is therefore an
    # identical capture and must not become a false revision.
    for key in _BROKER_VALUE_COLUMNS:
        if _normalize_value(existing[key]) != _normalize_value(incoming.get(key)):
            return True
    return False


def _record_revision(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    revision_type: str,
    existing_capture_hash: str = "",
) -> None:
    revision_id = hashlib.sha256(
        "|".join([
            str(row.get("symbol", "")),
            str(row.get("market_date", "")),
            str(row.get("broker_code", "")),
            str(row.get("side", "")),
            str(row.get("source", "")),
            str(row.get("capture_hash", "")),
        ]).encode("utf-8")
    ).hexdigest()
    conn.execute(
        """
        INSERT INTO broker_daily_revisions (
            revision_id, symbol, market_date, broker_code, side, source,
            capture_id, capture_hash, revision_type, existing_capture_hash,
            row_json, received_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, market_date, broker_code, side, source, capture_hash)
        DO NOTHING
        """,
        (
            revision_id,
            row.get("symbol"),
            row.get("market_date"),
            row.get("broker_code"),
            row.get("side"),
            row.get("source"),
            row.get("capture_id"),
            row.get("capture_hash"),
            revision_type,
            existing_capture_hash,
            _row_payload(row),
            row.get("received_at"),
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ),
    )


def upsert_broker_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    prepared_rows = [_prepared_row(row) for row in rows]
    for row in prepared_rows:
        if not all(str(row.get(key, "")).strip() for key in ("symbol", "market_date", "broker_code", "side", "source")):
            raise ValueError("BROKER_DAILY_IDENTITY_INCOMPLETE")
        row["capture_hash"] = str(row.get("capture_hash") or _capture_hash(row))
    sql = """
        INSERT INTO broker_daily
            (symbol, market_date, broker_code, broker_type, side, source,
             net_value, net_lot, gross_value, gross_lot, frequency, avg_price,
             rank, quality_status, received_at, capture_id, capture_hash)
        VALUES
            (:symbol, :market_date, :broker_code, :broker_type, :side, :source,
             :net_value, :net_lot, :gross_value, :gross_lot, :frequency, :avg_price,
             :rank, :quality_status, :received_at, :capture_id, :capture_hash)
        ON CONFLICT (symbol, market_date, broker_code, side, source)
        DO NOTHING
    """
    conflicts: list[dict[str, Any]] = []
    with transaction(conn):
        existing_rows: list[tuple[dict[str, Any], sqlite3.Row | None]] = []
        for row in prepared_rows:
            existing = conn.execute(
                "SELECT * FROM broker_daily WHERE symbol=? AND market_date=? AND broker_code=? AND side=? AND source=?",
                (
                    row.get("symbol"),
                    row.get("market_date"),
                    row.get("broker_code"),
                    row.get("side"),
                    row.get("source"),
                ),
            ).fetchone()
            existing_rows.append((row, existing))
            if existing is not None and _broker_row_conflicts(existing, row):
                conflicts.append({"row": row, "existing": existing})

        # Record all changed captures durably, but do not partially add new
        # canonical rows from a mixed batch.  The caller still receives the
        # conflict so production can fail closed.
        if conflicts:
            for item in conflicts:
                existing = item["existing"]
                existing_hash = str(existing["capture_hash"] or "") if existing is not None else ""
                _record_revision(
                    conn,
                    item["row"],
                    revision_type="REVISION",
                    existing_capture_hash=existing_hash,
                )
        else:
            for row, existing in existing_rows:
                if existing is None:
                    conn.execute(sql, row)
                else:
                    # Identical capture: deduplicate.  No UPDATE occurs.
                    _record_revision(conn, row, revision_type="DUPLICATE", existing_capture_hash=str(existing["capture_hash"] or ""))
    if conflicts:
        row = conflicts[0]["row"]
        raise RuntimeError(
            f"BROKER_DAILY_CONFLICT: symbol={row.get('symbol')} market_date={row.get('market_date')} "
            f"broker_code={row.get('broker_code')} side={row.get('side')} source={row.get('source')} "
            f"capture_hash={row.get('capture_hash')}"
        )
    # Preserve the historical return contract: callers use this as the number
    # of input records accepted/deduplicated, not as an INSERT rowcount.
    return len(rows)


def select_daily_period_rows(
    rows: list[dict[str, Any]],
    session_dates: Sequence[str],
) -> dict[str, Any]:
    """Select only real daily observations in an explicit session window.

    This helper is intentionally independent of any scoring engine.  It is
    used by internal rollups and tests to make the no-shift/no-synthetic-day
    rule explicit.
    """
    expected = [str(value)[:10] for value in session_dates if str(value).strip()]
    expected_set = set(expected)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        market_date = str(row.get("market_date", ""))[:10]
        if market_date in expected_set:
            by_date.setdefault(market_date, []).append(row)
    available = [value for value in expected if value in by_date]
    missing = [value for value in expected if value not in by_date]
    expected_count = len(expected)
    coverage = len(available) / expected_count if expected_count else 0.0
    return {
        "rows": [row for value in expected for row in by_date.get(value, [])],
        "session_dates": available,
        "missing_session_dates": missing,
        "coverage": coverage,
        "coverage_text": f"{len(available)}/{expected_count}",
        "status": "COMPLETE" if expected_count and not missing else "INCOMPLETE",
    }


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
