from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pandas as pd

from modules.database.swing_history_db import archive_prices, connect, init_schema


def _write_history(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def _row(day: str, close: float, *, volume: float = 1_000_000) -> dict:
    return {
        "Symbol": "BBCA",
        "Date": day,
        "Open": close - 2,
        "High": close + 3,
        "Low": close - 4,
        "Close": close,
        "Adj Close": close,
        "Volume": volume,
    }


def test_changed_file_archives_only_new_candle(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    csv_path = tmp_path / "BBCA.csv"

    first = [_row("2026-08-20", 100.0), _row("2026-08-21", 102.0)]
    second = [*first, _row("2026-08-24", 104.0)]

    with closing(connect(db_path)) as conn:
        init_schema(conn)
        _write_history(csv_path, first)
        assert archive_prices(conn, csv_path) == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM market_prices_daily_revisions"
        ).fetchone()[0] == 2

        _write_history(csv_path, second)
        # Whole-file SHA changed, but only the genuinely new candle is appended.
        assert archive_prices(conn, csv_path) == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM market_prices_daily_revisions"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM market_prices_daily"
        ).fetchone()[0] == 3

        per_date = dict(
            conn.execute(
                """
                SELECT price_date, COUNT(*)
                FROM market_prices_daily_revisions
                GROUP BY price_date
                ORDER BY price_date
                """
            ).fetchall()
        )
        assert per_date == {
            "2026-08-20": 1,
            "2026-08-21": 1,
            "2026-08-24": 1,
        }

        # Both physical file revisions remain auditable.
        assert conn.execute(
            """
            SELECT COUNT(*) FROM archived_source_files
            WHERE dataset_type='market_prices_daily'
            """
        ).fetchone()[0] == 2

        # Exact same file revision is still skipped at file level.
        assert archive_prices(conn, csv_path) == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM market_prices_daily_revisions"
        ).fetchone()[0] == 3


def test_historical_correction_appends_only_corrected_row(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    csv_path = tmp_path / "BBCA.csv"

    initial = [_row("2026-08-20", 100.0), _row("2026-08-21", 102.0)]
    corrected = [_row("2026-08-20", 101.0), _row("2026-08-21", 102.0)]

    with closing(connect(db_path)) as conn:
        init_schema(conn)
        _write_history(csv_path, initial)
        assert archive_prices(conn, csv_path) == 2

        _write_history(csv_path, corrected)
        assert archive_prices(conn, csv_path) == 1

        counts = dict(
            conn.execute(
                """
                SELECT price_date, COUNT(*)
                FROM market_prices_daily_revisions
                GROUP BY price_date
                ORDER BY price_date
                """
            ).fetchall()
        )
        assert counts == {"2026-08-20": 2, "2026-08-21": 1}

        current = conn.execute(
            """
            SELECT close FROM market_prices_daily
            WHERE symbol='BBCA' AND price_date='2026-08-20' AND source='YAHOO'
            """
        ).fetchone()
        assert current == (101.0,)

        revisions = conn.execute(
            """
            SELECT close FROM market_prices_daily_revisions
            WHERE symbol='BBCA' AND price_date='2026-08-20' AND source='YAHOO'
            ORDER BY revision_sequence
            """
        ).fetchall()
        assert revisions == [(100.0,), (101.0,)]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
