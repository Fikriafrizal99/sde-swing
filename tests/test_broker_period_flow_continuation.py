from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from modules.broker_bridge.broker_period_context import custom_period_spec, fixed_period_spec, period_source_for
from modules.data_sources.broker_history import connect, init_schema, upsert_broker_rows
from modules.database import swing_history_db
from tools import run_final_watchlist_broker_period as runner


def test_period_specs_keep_exact_stockbit_periods_as_primary() -> None:
    five = fixed_period_spec("5D", "2026-08-11")
    custom = custom_period_spec(five.period_start, five.period_end)

    assert five.session_dates == custom.session_dates
    assert five.trading_sessions == 5
    assert period_source_for("1D") == "STOCKBIT_1D"
    assert period_source_for("3D") == "STOCKBIT_AGGREGATE_EXPORT"
    assert period_source_for("CUSTOM") == "STOCKBIT_AGGREGATE_EXPORT"


def test_final_watchlist_period_wrapper_does_not_require_history_database() -> None:
    source = inspect.getsource(runner.main)

    assert "capture_exact_aggregate_primary" in source
    for retired_runtime_dependency in (
        "connect_broker_history",
        "daily_history_coverage",
        "get_trading_sessions_before",
        "ingest_daily_capture_files",
        "run_broker_multiday",
    ):
        assert retired_runtime_dependency not in source


def _daily_row(*, value: float = 100.0) -> dict[str, object]:
    return {
        "symbol": "AAA",
        "market_date": "2026-08-11",
        "broker_code": "B1",
        "broker_type": "DOMESTIK",
        "side": "BUY",
        "net_value": value,
        "net_lot": 1,
        "gross_value": value,
        "gross_lot": 1,
        "frequency": 1,
        "avg_price": 100,
        "rank": 1,
        "source": "STOCKBIT_1D",
        "quality_status": "VALIDATED",
        "received_at": "2026-08-11T09:00:00+07:00",
    }


def test_daily_history_archive_remains_immutable_and_independent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "broker.db")
    init_schema(conn)
    try:
        row = _daily_row()
        assert upsert_broker_rows(conn, [row]) == 1

        duplicate = dict(row, received_at="2026-08-11T10:00:00+07:00")
        assert upsert_broker_rows(conn, [duplicate]) == 1
        assert conn.execute("SELECT COUNT(*) FROM broker_daily").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM broker_daily_revisions WHERE revision_type='DUPLICATE'"
        ).fetchone()[0] == 1

        with pytest.raises(RuntimeError, match="BROKER_DAILY_CONFLICT"):
            upsert_broker_rows(conn, [dict(row, net_value=999.0)])
    finally:
        conn.close()


def test_generic_history_archive_rejects_unsafe_sql_identifiers(tmp_path: Path) -> None:
    conn = swing_history_db.connect(tmp_path / "history.db")
    try:
        conn.execute("CREATE TABLE safe_table (key_id TEXT PRIMARY KEY, value TEXT)")
        with pytest.raises(ValueError, match="UNSAFE_SQL_IDENTIFIER"):
            swing_history_db.insert_if_missing(
                conn,
                "safe_table; DROP TABLE safe_table",
                {"key_id": "a", "value": "b"},
                ["key_id"],
            )
    finally:
        conn.close()
