from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.database.swing_history_db import connect, init_schema
from modules.portfolio.broker_portfolio_backfill import archive_backfill
from modules.portfolio.portfolio_broker_daily import sync_tasks


ROOT = Path(__file__).resolve().parents[1]


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


def test_windows_menus_expose_simple_daily_flow_and_auto_sync():
    broker_menu = (ROOT / "maintenance/BACKFILL_PORTFOLIO_BROKER.bat").read_text(encoding="utf-8-sig")
    portfolio_menu = (ROOT / "maintenance/RECORD_PORTFOLIO_BUY.bat").read_text(encoding="utf-8-sig")

    assert "[1] UPDATE HARIAN" in broker_menu
    assert "[2] IMPORT HASIL Tampermonkey" in broker_menu
    assert "[9] ADVANCED" in broker_menu
    assert "portfolio_broker_daily.py" in broker_menu
    assert "call :SYNC_BROKER_TASKS" in portfolio_menu
    assert "Broker task otomatis disinkronkan setelah BUY / SELL / edit posisi." in portfolio_menu
