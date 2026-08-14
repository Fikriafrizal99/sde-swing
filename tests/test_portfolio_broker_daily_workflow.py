from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from modules.database.swing_history_db import connect, init_schema
from modules.portfolio import broker_portfolio_backfill as backfill
from modules.portfolio import portfolio_broker_daily as daily
from modules.portfolio.broker_portfolio_backfill import archive_backfill
from modules.portfolio.portfolio_broker_daily import resolve_completed_broker_date, sync_tasks


ROOT = Path(__file__).resolve().parents[1]
JAKARTA = ZoneInfo("Asia/Jakarta")


def _summary_row(symbol: str, day: str) -> dict:
    return {
        "FROM_DATE": day,
        "TO_DATE": day,
        "EMITEN": symbol,
        "TOTAL_BUY": 150.0,
        "TOTAL_SELL": 50.0,
        "NET_FLOW": 100.0,
        "TOP_BUYER_1": "YP",
        "TOP_SELLER_1": "AK",
        "BUYER_CONCENTRATION": 0.70,
        "SELLER_CONCENTRATION": 0.30,
    }


def _insert_open(conn, symbol: str, buy_date: str) -> None:
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO portfolio_positions (
            position_id, signal_id, symbol, buy_date, quantity, buy_price,
            current_status, notes, created_at, updated_at
        ) VALUES (?, NULL, ?, ?, 100, 1000, 'OPEN', '', ?, ?)
        """,
        (f"pos-{symbol}", symbol, buy_date, buy_date, buy_date),
    )
    conn.commit()


def test_daily_sync_only_keeps_dates_missing_from_database(tmp_path):
    db = tmp_path / "history.db"
    output = tmp_path / "tasks.csv"
    archive_root = tmp_path / "archive"
    conn = connect(db)
    try:
        _insert_open(conn, "TINS", "2026-08-03")
        archive_backfill(
            conn,
            pd.DataFrame([
                _summary_row("TINS", "2026-08-03"),
                _summary_row("TINS", "2026-08-04"),
            ]),
            source_path=tmp_path / "seed.csv",
            archive_root=archive_root,
        )
    finally:
        conn.close()

    tasks, meta, coverage = sync_tasks(
        db_path=db,
        calendar_path=ROOT / "config/trading_calendar.json",
        output_path=output,
        to_date="2026-08-05",
    )

    assert [row["TO_DATE"] for row in tasks] == ["2026-08-05"]
    assert meta["task_count"] == 1
    assert coverage[0]["available"] == 2
    assert coverage[0]["missing_dates"] == ["2026-08-05"]


def test_sync_clears_stale_tasks_after_history_complete_or_position_closed(tmp_path):
    db = tmp_path / "history.db"
    output = tmp_path / "tasks.csv"
    archive_root = tmp_path / "archive"
    conn = connect(db)
    try:
        _insert_open(conn, "TINS", "2026-08-03")
        archive_backfill(
            conn,
            pd.DataFrame([
                _summary_row("TINS", "2026-08-03"),
                _summary_row("TINS", "2026-08-04"),
                _summary_row("TINS", "2026-08-05"),
            ]),
            source_path=tmp_path / "complete.csv",
            archive_root=archive_root,
        )
    finally:
        conn.close()

    tasks, meta, _ = sync_tasks(
        db_path=db,
        calendar_path=ROOT / "config/trading_calendar.json",
        output_path=output,
        to_date="2026-08-05",
    )
    assert tasks == []
    assert meta["status"] == "NO_MISSING_DATES"
    assert pd.read_csv(output).empty

    conn = connect(db)
    try:
        conn.execute("UPDATE portfolio_positions SET current_status='CLOSED' WHERE symbol='TINS'")
        conn.commit()
    finally:
        conn.close()

    tasks, meta, coverage = sync_tasks(
        db_path=db,
        calendar_path=ROOT / "config/trading_calendar.json",
        output_path=output,
        to_date="2026-08-05",
    )
    assert tasks == []
    assert meta["status"] == "NO_OPEN_POSITION"
    assert coverage == []
    assert pd.read_csv(output).empty


def test_midnight_next_calendar_day_does_not_create_missing_today():
    target, meta = resolve_completed_broker_date(
        calendar_path=ROOT / "config/trading_calendar.json",
        scheduler_path=ROOT / "config/scheduler.json",
        now=datetime(2026, 8, 13, 0, 28, tzinfo=JAKARTA),
    )

    assert target == "2026-08-12"
    assert meta["target_reason"] == "TODAY_SESSION_NOT_COMPLETED"
    assert meta["data_ready_time"] == "16:30"


def test_today_becomes_eligible_only_after_post_market_cutoff():
    before_target, _ = resolve_completed_broker_date(
        calendar_path=ROOT / "config/trading_calendar.json",
        scheduler_path=ROOT / "config/scheduler.json",
        now=datetime(2026, 8, 13, 15, 0, tzinfo=JAKARTA),
    )
    after_target, meta = resolve_completed_broker_date(
        calendar_path=ROOT / "config/trading_calendar.json",
        scheduler_path=ROOT / "config/scheduler.json",
        now=datetime(2026, 8, 13, 16, 31, tzinfo=JAKARTA),
    )

    assert before_target == "2026-08-12"
    assert after_target == "2026-08-13"
    assert meta["target_reason"] == "TODAY_SESSION_COMPLETED"


def test_daily_sync_at_midnight_caps_tasks_at_previous_completed_session(tmp_path):
    db = tmp_path / "history.db"
    output = tmp_path / "tasks.csv"
    archive_root = tmp_path / "archive"
    conn = connect(db)
    try:
        _insert_open(conn, "TINS", "2026-08-11")
        archive_backfill(
            conn,
            pd.DataFrame([
                _summary_row("TINS", "2026-08-11"),
                _summary_row("TINS", "2026-08-12"),
            ]),
            source_path=tmp_path / "complete.csv",
            archive_root=archive_root,
        )
    finally:
        conn.close()

    tasks, meta, coverage = sync_tasks(
        db_path=db,
        calendar_path=ROOT / "config/trading_calendar.json",
        scheduler_path=ROOT / "config/scheduler.json",
        output_path=output,
        now=datetime(2026, 8, 13, 0, 28, tzinfo=JAKARTA),
    )

    assert meta["to_date"] == "2026-08-12"
    assert tasks == []
    assert coverage[0]["missing"] == 0
    assert pd.read_csv(output).empty


def test_windows_menus_expose_simple_daily_flow_and_auto_sync():
    broker_menu = (ROOT / "maintenance/BACKFILL_PORTFOLIO_BROKER.bat").read_text(encoding="utf-8-sig")
    portfolio_menu = (ROOT / "maintenance/RECORD_PORTFOLIO_BUY.bat").read_text(encoding="utf-8-sig")

    assert "[1] UPDATE HARIAN" in broker_menu
    assert "[2] IMPORT HASIL ke database" in broker_menu
    assert "[9] ADVANCED" in broker_menu
    assert "portfolio_broker_daily.py" in broker_menu
    assert "call :SYNC_BROKER_TASKS" in portfolio_menu
    assert "Broker task otomatis disinkronkan setelah BUY / SELL / edit posisi." in portfolio_menu


def test_daily_sync_uses_one_schema_boundary_and_one_batched_coverage_query(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "history.db"
    output = tmp_path / "tasks.csv"
    archive_root = tmp_path / "archive"
    symbols = ["BBCA", "BBRI", "TLKM"]
    conn = connect(db)
    try:
        for symbol in symbols:
            _insert_open(conn, symbol, "2026-08-12")
        archive_backfill(
            conn,
            pd.DataFrame([_summary_row(symbol, "2026-08-12") for symbol in symbols]),
            source_path=tmp_path / "daily.csv",
            archive_root=archive_root,
        )
    finally:
        conn.close()

    calls = {"init_schema": 0, "batch_query": 0}
    original_init = daily.init_schema
    original_batch = backfill.existing_broker_dates_by_symbol

    def counted_init(conn):
        calls["init_schema"] += 1
        return original_init(conn)

    def counted_batch(conn, date_ranges, **kwargs):
        calls["batch_query"] += 1
        assert set(date_ranges) == set(symbols)
        return original_batch(conn, date_ranges, **kwargs)

    monkeypatch.setattr(daily, "init_schema", counted_init)
    monkeypatch.setattr(backfill, "existing_broker_dates_by_symbol", counted_batch)

    tasks, meta, coverage = daily.sync_tasks(
        db_path=db,
        calendar_path=ROOT / "config/trading_calendar.json",
        output_path=output,
        to_date="2026-08-13",
    )

    assert calls == {"init_schema": 1, "batch_query": 1}
    assert {(row["Symbol"], row["TO_DATE"]) for row in tasks} == {
        (symbol, "2026-08-13") for symbol in symbols
    }
    assert meta["skipped_existing"] == 3
    assert {row["symbol"]: row["available"] for row in coverage} == {
        symbol: 1 for symbol in symbols
    }
