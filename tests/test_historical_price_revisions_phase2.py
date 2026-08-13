from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest

from modules.database.swing_history_db import archive_prices, connect, init_schema
from swing_utils import file_sha256


LEGACY_MARKET_PRICE_SCHEMA = """
CREATE TABLE market_prices_daily (
    symbol TEXT NOT NULL,
    price_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adjusted_close REAL,
    volume REAL,
    source TEXT NOT NULL,
    source_revision TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (symbol, price_date, source)
)
"""


def _write_price(path: Path, close: float) -> str:
    pd.DataFrame(
        [
            {
                "Symbol": "BBCA",
                "Date": "2026-08-12",
                "Open": 100.0,
                "High": max(106.0, close),
                "Low": 99.0,
                "Close": close,
                "Adj Close": close,
                "Volume": 1_000_000,
            }
        ]
    ).to_csv(path, index=False)
    return file_sha256(path, short=True)


def test_legacy_price_table_migrates_and_new_revisions_are_append_only(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    with closing(connect(db_path)) as conn:
        conn.execute(LEGACY_MARKET_PRICE_SCHEMA)
        conn.execute(
            """
            INSERT INTO market_prices_daily (
                symbol, price_date, open, high, low, close, adjusted_close,
                volume, source, source_revision, created_at, updated_at
            ) VALUES ('BBCA', '2026-08-12', 100, 106, 99, 105, 105,
                      1000000, 'YAHOO', 'REV-A', '2026-08-12T18:00:00',
                      '2026-08-12T18:00:00')
            """
        )
        conn.commit()

        init_schema(conn)
        migrated = conn.execute(
            """
            SELECT source_revision, close, source_path
            FROM market_prices_daily_revisions
            ORDER BY revision_sequence
            """
        ).fetchall()
        assert migrated == [("REV-A", 105.0, "LEGACY_MIGRATION")]

        csv_path = tmp_path / "BBCA.csv"
        revision_b = _write_price(csv_path, 125.0)
        assert archive_prices(conn, csv_path) == 1

        revisions = conn.execute(
            """
            SELECT source_revision, close, revision_sequence
            FROM market_prices_daily_revisions
            WHERE symbol='BBCA' AND price_date='2026-08-12' AND source='YAHOO'
            ORDER BY revision_sequence
            """
        ).fetchall()
        assert len(revisions) == 2
        assert [(row[0], row[1]) for row in revisions] == [
            ("REV-A", 105.0),
            (revision_b, 125.0),
        ]

        current = conn.execute(
            """
            SELECT source_revision, close, revision_sequence
            FROM market_prices_daily
            WHERE symbol='BBCA' AND price_date='2026-08-12' AND source='YAHOO'
            """
        ).fetchone()
        assert current == (revision_b, 125.0, revisions[-1][2])
        latest_view = conn.execute(
            """
            SELECT source_revision, close, revision_sequence
            FROM market_prices_daily_latest
            WHERE symbol='BBCA' AND price_date='2026-08-12' AND source='YAHOO'
            """
        ).fetchone()
        assert latest_view == current

        assert archive_prices(conn, csv_path) == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM market_prices_daily_revisions"
        ).fetchone()[0] == 2
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        with pytest.raises(sqlite3.IntegrityError, match="MARKET_PRICE_REVISION_APPEND_ONLY"):
            conn.execute(
                "UPDATE market_prices_daily_revisions SET close=999 WHERE source_revision='REV-A'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="MARKET_PRICE_REVISION_APPEND_ONLY"):
            conn.execute(
                "DELETE FROM market_prices_daily_revisions WHERE source_revision='REV-A'"
            )
def test_fresh_schema_current_projection_tracks_latest_archived_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    csv_path = tmp_path / "BBRI.csv"
    with closing(connect(db_path)) as conn:
        init_schema(conn)
        revision_a = _write_price(csv_path, 110.0)
        assert archive_prices(conn, csv_path) == 1
        revision_b = _write_price(csv_path, 115.0)
        assert archive_prices(conn, csv_path) == 1

        history = conn.execute(
            """
            SELECT source_revision, close
            FROM market_prices_daily_revisions
            ORDER BY revision_sequence
            """
        ).fetchall()
        assert history == [(revision_a, 110.0), (revision_b, 115.0)]
        assert conn.execute(
            "SELECT source_revision, close FROM market_prices_daily"
        ).fetchone() == (revision_b, 115.0)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
