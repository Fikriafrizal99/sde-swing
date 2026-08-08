from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from modules.broker_bridge.wait_for_broker_export import files_for_scan
from modules.database.swing_history_db import connect, init_schema
from modules.portfolio.broker_portfolio_backfill import (
    archive_backfill,
    build_tasks,
    validate_backfill_dataframe,
)


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
        "TOP_BUYER_1_VALUE": 80.0,
        "TOP_BUYER_2": "CC",
        "TOP_BUYER_2_VALUE": 40.0,
        "TOP_BUYER_3": "XL",
        "TOP_BUYER_3_VALUE": 30.0,
        "TOP_SELLER_1": "AK",
        "TOP_SELLER_1_VALUE": -25.0,
        "TOP_SELLER_2": "PD",
        "TOP_SELLER_2_VALUE": -15.0,
        "TOP_SELLER_3": "NI",
        "TOP_SELLER_3_VALUE": -10.0,
        "BUYER_CONCENTRATION": 0.70,
        "SELLER_CONCENTRATION": 0.30,
        "BROKER_ACCDIST": "BIG ACC",
        "AVG_ACCDIST": "BIG ACC",
        "AVG_AMOUNT": 1.0,
        "AVG_PERCENT": 1.0,
        "TOP3_ACCDIST": "BIG ACC",
        "TOP3_AMOUNT": 1.0,
        "TOP3_PERCENT": 1.0,
        "TOTAL_BUYER_COUNT": 10,
        "TOTAL_SELLER_COUNT": 8,
        "TOTAL_VALUE": 200.0,
        "TOTAL_VOLUME": 1000.0,
    }


def _insert_open_position(conn: sqlite3.Connection, symbol: str, buy_date: str) -> None:
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


def test_prepare_open_position_creates_one_task_per_trading_day_and_skips_weekend(tmp_path):
    db = tmp_path / "history.db"
    conn = connect(db)
    try:
        _insert_open_position(conn, "TINS", "2026-08-03")
        tasks, meta = build_tasks(
            conn,
            symbol="TINS",
            to_date="2026-08-08",
            calendar_path=ROOT / "config/trading_calendar.json",
        )
    finally:
        conn.close()

    assert [row["TO_DATE"] for row in tasks] == [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
    ]
    assert all(row["FROM_DATE"] == row["TO_DATE"] for row in tasks)
    assert all(row["Symbol"] == "TINS" for row in tasks)
    assert meta["task_count"] == 5


def test_prepare_manual_symbol_outside_final_watchlist_is_supported(tmp_path):
    db = tmp_path / "history.db"
    conn = connect(db)
    try:
        init_schema(conn)
        tasks, meta = build_tasks(
            conn,
            symbol="ABCD",
            from_date="2026-08-05",
            to_date="2026-08-07",
            calendar_path=ROOT / "config/trading_calendar.json",
        )
    finally:
        conn.close()

    assert meta["requested_symbols"] == 1
    assert [row["Symbol"] for row in tasks] == ["ABCD", "ABCD", "ABCD"]
    assert all(row["SOURCE"] == "MANUAL" for row in tasks)


def test_prepare_skips_dates_already_present_in_shared_broker_database(tmp_path):
    db = tmp_path / "history.db"
    archive_root = tmp_path / "archive"
    conn = connect(db)
    try:
        _insert_open_position(conn, "TINS", "2026-08-03")
        archive_backfill(
            conn,
            pd.DataFrame([_summary_row("TINS", "2026-08-04")]),
            source_path=tmp_path / "seed.csv",
            archive_root=archive_root,
        )
        tasks, meta = build_tasks(
            conn,
            symbol="TINS",
            to_date="2026-08-05",
            calendar_path=ROOT / "config/trading_calendar.json",
        )
    finally:
        conn.close()

    assert [row["TO_DATE"] for row in tasks] == ["2026-08-03", "2026-08-05"]
    assert meta["skipped_existing"] == 1


def test_force_prepare_can_refresh_existing_dates_for_actor_nominals(tmp_path):
    db = tmp_path / "history.db"
    archive_root = tmp_path / "archive"
    conn = connect(db)
    try:
        _insert_open_position(conn, "TINS", "2026-08-03")
        archive_backfill(
            conn,
            pd.DataFrame([_summary_row("TINS", "2026-08-03")]),
            source_path=tmp_path / "seed.csv",
            archive_root=archive_root,
        )
        tasks, meta = build_tasks(
            conn,
            symbol="TINS",
            to_date="2026-08-03",
            calendar_path=ROOT / "config/trading_calendar.json",
            force=True,
        )
    finally:
        conn.close()
    assert [row["TO_DATE"] for row in tasks] == ["2026-08-03"]
    assert meta["force"] is True


def test_import_rejects_cumulative_range_instead_of_faking_daily_history():
    row = _summary_row("TINS", "2026-08-07")
    row["FROM_DATE"] = "2026-08-03"
    with pytest.raises(ValueError, match="BACKFILL_REJECTED_NOT_DAILY"):
        validate_backfill_dataframe(pd.DataFrame([row]))


def test_import_groups_multi_date_export_into_separate_canonical_snapshots_and_keeps_actor_values(tmp_path):
    db = tmp_path / "history.db"
    archive_root = tmp_path / "archive"
    source = tmp_path / "BROKER_PORTFOLIO_BACKFILL_SUMMARY_2026-08-07.csv"
    df = pd.DataFrame(
        [
            _summary_row("TINS", "2026-08-06"),
            _summary_row("ABCD", "2026-08-06"),
            _summary_row("TINS", "2026-08-07"),
        ]
    )
    df.to_csv(source, index=False)

    conn = connect(db)
    try:
        results = archive_backfill(
            conn,
            df,
            source_path=source,
            archive_root=archive_root,
        )
        snapshots = conn.execute(
            "SELECT broker_date, data_quality_status FROM broker_snapshots ORDER BY broker_date"
        ).fetchall()
        summary_rows = conn.execute(
            """
            SELECT s.broker_date, b.symbol, b.row_json
            FROM broker_summary b
            JOIN broker_snapshots s ON s.broker_snapshot_id=b.broker_snapshot_id
            ORDER BY s.broker_date, b.symbol
            """
        ).fetchall()
    finally:
        conn.close()

    assert len(results) == 2
    assert snapshots == [
        ("2026-08-06", "VALID_BACKFILL_DAILY"),
        ("2026-08-07", "VALID_BACKFILL_DAILY"),
    ]
    assert [(row[0], row[1]) for row in summary_rows] == [
        ("2026-08-06", "ABCD"),
        ("2026-08-06", "TINS"),
        ("2026-08-07", "TINS"),
    ]
    assert '"TOP_BUYER_1_VALUE": 80.0' in summary_rows[0][2]
    assert '"TOP_SELLER_1_VALUE": -25.0' in summary_rows[0][2]


def test_normal_final_watchlist_broker_scanner_ignores_backfill_exports(tmp_path):
    normal = tmp_path / "BROKER_SUMMARY_COMBINED_2026-08-07.csv"
    backfill = tmp_path / "BROKER_PORTFOLIO_BACKFILL_SUMMARY_2026-08-07.csv"
    normal.write_text("x\n1\n", encoding="utf-8")
    backfill.write_text("x\n1\n", encoding="utf-8")

    scanned = files_for_scan(tmp_path, started=0.0, include_existing=True)
    assert normal in scanned
    assert backfill not in scanned


def test_backfill_tampermonkey_v2_uses_dedicated_namespace_and_exports_actor_values():
    source = (ROOT / "tampermonkey/Stockbit_Broker_Portfolio_Backfill_v2.user.js").read_text(
        encoding="utf-8"
    )
    assert "sde_broker_portfolio_backfill_v2_db" in source
    assert "BROKER_PORTFOLIO_BACKFILL_SUMMARY_" in source
    assert "TOP_BUYER_1_VALUE" in source
    assert "TOP_SELLER_1_VALUE" in source
    assert "BROKER_SUMMARY_COMBINED_" not in source


def test_launcher_exposes_backfill_as_maintenance_not_integrated_final_watchlist_job():
    launcher = (ROOT / "START_SDE_SWING.bat").read_text(encoding="utf-8-sig")
    assert "15. Backfill Broker Portfolio" in launcher
    assert "call maintenance\\BACKFILL_PORTFOLIO_BROKER.bat" in launcher
    section = launcher.split(":PORTFOLIO_BROKER_BACKFILL", 1)[1].split(":RUN_JOB", 1)[0]
    assert "run_sde_job_integrated.py" not in section
