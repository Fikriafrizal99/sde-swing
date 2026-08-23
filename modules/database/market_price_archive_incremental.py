from __future__ import annotations

"""Incremental historical-price archive for the SDE Swing SQLite history DB.

The historical CSV remains the auditable source file. A changed file is still
read in full so an old candle correction cannot be missed, but rows whose
canonical OHLCV values are identical to the current SQLite projection are not
re-appended as revisions just because the whole-file SHA changed.

This module is database-only. It does not feed or modify Technical, Candidate,
Broker, Decision, Entry/Exit, Lifecycle, or Portfolio calculations.
"""

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from modules.database import swing_history_db_baseline as baseline


_CURRENT_COLUMNS = (
    "symbol",
    "price_date",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
    "source",
)
_NUMERIC_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
)


def _chunks(values: list[str], size: int = 200) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _canonical_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return 0.0 if number == 0 else number


def _comparison_row_hash(row: dict[str, Any]) -> str:
    """Hash market values with stable Python/SQLite numeric representation.

    SQLite REAL values are returned as floats, while pandas may keep integer-looking
    CSV columns as ints.  The comparison hash therefore normalizes all numeric
    fields to float before hashing so 9000 and 9000.0 are treated as the same
    market fact.  This hash is only an incremental comparison key; baseline's
    append-only revision identity remains unchanged.
    """
    payload: dict[str, Any] = {
        "symbol": str(row.get("symbol") or ""),
        "price_date": str(row.get("price_date") or ""),
        "source": str(row.get("source") or ""),
    }
    for column in _NUMERIC_COLUMNS:
        payload[column] = _canonical_number(row.get(column))
    rendered = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _current_row_hashes(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], str]:
    """Load current row hashes with one query per small symbol chunk.

    The previous archiver performed INSERT + SELECT + UPSERT for every historical
    candle whenever a CSV file SHA changed. This projection lets us compare the
    incoming rows first and reserve revision writes for genuinely new/corrected
    candles only.
    """
    symbols = sorted({str(row.get("symbol") or "") for row in rows if row.get("symbol")})
    sources = sorted({str(row.get("source") or "") for row in rows if row.get("source")})
    if not symbols or not sources:
        return {}

    result: dict[tuple[str, str, str], str] = {}
    for source in sources:
        for symbol_chunk in _chunks(symbols):
            placeholders = ",".join("?" for _ in symbol_chunk)
            found = conn.execute(
                f"""
                SELECT {','.join(_CURRENT_COLUMNS)}
                FROM market_prices_daily
                WHERE source=? AND symbol IN ({placeholders})
                """,
                [source, *symbol_chunk],
            ).fetchall()
            for values in found:
                current = dict(zip(_CURRENT_COLUMNS, values))
                key = (
                    str(current.get("symbol") or ""),
                    str(current.get("price_date") or ""),
                    str(current.get("source") or ""),
                )
                result[key] = _comparison_row_hash(current)
    return result


def _rows_from_historical_file(
    file: Path,
    *,
    source: str,
    source_revision: str,
    archived_at: str,
) -> list[dict[str, Any]]:
    df = baseline.load_csv(file)
    if df.empty:
        return []

    date_col = baseline.find_col(df, "Date")
    open_col = baseline.find_col(df, "Open")
    high_col = baseline.find_col(df, "High")
    low_col = baseline.find_col(df, "Low")
    close_col = baseline.find_col(df, "Close")
    volume_col = baseline.find_col(df, "Volume")
    if not all([date_col, open_col, high_col, low_col, close_col, volume_col]):
        return []

    symbol_col = baseline.find_col(df, "Symbol", "Ticker", "EMITEN")
    adj_col = baseline.find_col(df, "Adj Close", "Adjusted_Close", "Adj_Close")
    parsed_date = pd.to_datetime(df[date_col], errors="coerce")
    close_series = pd.to_numeric(df[close_col], errors="coerce")
    valid = parsed_date.notna() & close_series.notna()
    if not valid.any():
        return []

    if symbol_col:
        symbols = df.loc[valid, symbol_col].map(baseline.normalize_symbol)
    else:
        symbols = pd.Series(
            [baseline.normalize_symbol(file.stem)] * int(valid.sum()),
            index=df.index[valid],
        )

    adjusted_close = pd.to_numeric(df[adj_col], errors="coerce") if adj_col else close_series
    adjusted_close = adjusted_close.fillna(close_series)
    price_df = pd.DataFrame(
        {
            "symbol": symbols,
            "price_date": parsed_date.loc[valid].dt.date.astype(str),
            "open": pd.to_numeric(df.loc[valid, open_col], errors="coerce"),
            "high": pd.to_numeric(df.loc[valid, high_col], errors="coerce"),
            "low": pd.to_numeric(df.loc[valid, low_col], errors="coerce"),
            "close": close_series.loc[valid],
            "adjusted_close": adjusted_close.loc[valid],
            "volume": pd.to_numeric(df.loc[valid, volume_col], errors="coerce"),
            "source": source,
            "source_revision": source_revision,
            "created_at": archived_at,
            "updated_at": archived_at,
        }
    )
    return price_df.where(pd.notna(price_df), None).to_dict("records")


def archive_prices(
    conn: sqlite3.Connection,
    historical_dir: Path,
    source: str = "YAHOO",
) -> int:
    """Archive only new/corrected market-price rows.

    File SHA remains the first-level audit/idempotency key. When the file SHA
    changes, every row is compared to the current DB projection using a stable
    canonical value hash that excludes source_revision and created/updated
    timestamps. Unchanged rows therefore cause no revision INSERT,
    revision-sequence SELECT, or current-row UPSERT.

    The return value is the number of *newly appended revisions*, rather than
    the number of rows merely scanned from changed files.
    """
    if not historical_dir.exists():
        return 0

    files = (
        sorted(historical_dir.glob("*.csv"))
        if historical_dir.is_dir()
        else [historical_dir]
    )
    file_total = len(files)
    archived_rows = 0
    unchanged_rows = 0
    scanned_rows = 0
    skipped_files = 0
    changed_files = 0
    now = datetime.now().isoformat(timespec="seconds")

    print(f"[DB] Market prices: {file_total} file historical (incremental row archive)", flush=True)

    for index, file in enumerate(files, start=1):
        revision = baseline.file_sha256(file, short=True)
        if baseline.source_file_already_archived(
            conn,
            "market_prices_daily",
            file,
            revision,
        ):
            skipped_files += 1
        else:
            rows = _rows_from_historical_file(
                file,
                source=source,
                source_revision=revision,
                archived_at=now,
            )
            if rows:
                changed_files += 1
                scanned_rows += len(rows)
                current_hashes = _current_row_hashes(conn, rows)
                file_archived_rows = 0
                file_unchanged_rows = 0

                for row in rows:
                    key = (
                        str(row.get("symbol") or ""),
                        str(row.get("price_date") or ""),
                        str(row.get("source") or ""),
                    )
                    incoming_hash = _comparison_row_hash(row)
                    if current_hashes.get(key) == incoming_hash:
                        file_unchanged_rows += 1
                        continue

                    inserted, _revision_id, _sequence = baseline.append_market_price_revision(
                        conn,
                        row,
                        source_path=str(file.resolve()),
                        archived_at=now,
                        publish_current=True,
                    )
                    if inserted:
                        file_archived_rows += 1
                        current_hashes[key] = incoming_hash

                archived_rows += file_archived_rows
                unchanged_rows += file_unchanged_rows
                baseline.mark_source_file_archived(
                    conn,
                    "market_prices_daily",
                    file,
                    revision,
                    len(rows),
                )
            else:
                # Keep baseline behavior for invalid/empty files: do not mark
                # them archived so a repaired source can be processed later.
                pass

        if index % 50 == 0 or index == file_total:
            conn.commit()
            print(
                "[DB] Market prices progress: "
                f"{index}/{file_total} file, "
                f"archived_rows={archived_rows}, "
                f"unchanged_rows={unchanged_rows}, "
                f"skipped_files={skipped_files}, "
                f"changed_files={changed_files}",
                flush=True,
            )

    conn.commit()
    print(
        "[DB] Market prices incremental complete: "
        f"scanned_rows={scanned_rows}, archived_rows={archived_rows}, "
        f"unchanged_rows={unchanged_rows}, skipped_files={skipped_files}",
        flush=True,
    )
    return archived_rows
