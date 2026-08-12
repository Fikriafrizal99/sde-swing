from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from modules.database.swing_history_db import connect
from modules.portfolio.broker_history_context import load_broker_history
from modules.portfolio.broker_portfolio_backfill import archive_backfill
from modules.portfolio.repair_legacy_broker_backfill_provenance import (
    repair_legacy_backfill_provenance,
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


def test_legacy_validated_backfill_becomes_visible_to_portfolio_history(tmp_path):
    db = tmp_path / "history.db"
    archive_root = tmp_path / "archive"
    day = "2026-08-12"
    conn = connect(db)
    try:
        archive_backfill(
            conn,
            pd.DataFrame([_summary_row("MDKA", day)]),
            source_path=tmp_path / "legacy.csv",
            archive_root=archive_root,
        )
        snapshot_id = conn.execute(
            "SELECT broker_snapshot_id FROM broker_snapshots WHERE broker_date=?",
            (day,),
        ).fetchone()[0]

        legacy_manifest = {
            "broker_date": day,
            "from_date": day,
            "to_date": day,
            "source": "PORTFOLIO_BACKFILL",
            "coverage": 1.0,
            "DATA_QUALITY_STATUS": "VALID_BACKFILL_DAILY",
            "daily_only": True,
            "symbol_count": 1,
        }
        conn.execute(
            "UPDATE broker_snapshots SET manifest_json=? WHERE broker_snapshot_id=?",
            (json.dumps(legacy_manifest), snapshot_id),
        )
        conn.commit()

        assert load_broker_history(conn, "MDKA", day, day) == []

        repaired = repair_legacy_backfill_provenance(conn)
        history = load_broker_history(conn, "MDKA", day, day)
        manifest = json.loads(
            conn.execute(
                "SELECT manifest_json FROM broker_snapshots WHERE broker_snapshot_id=?",
                (snapshot_id,),
            ).fetchone()[0]
        )
    finally:
        conn.close()

    assert repaired == 1
    assert len(history) == 1
    assert history[0]["broker_date"] == day
    assert manifest["broker_period_type"] == "1D"
    assert manifest["broker_period_source"] == "STOCKBIT_1D"
    assert manifest["daily_history_eligible"] is True
    assert manifest["aggregate_snapshot"] is False
    assert manifest["provenance_repair"] == "LEGACY_PROVEN_DAILY_V3"


def test_blank_snapshot_dates_are_recovered_only_when_row_json_proves_daily(tmp_path):
    db = tmp_path / "history.db"
    archive_root = tmp_path / "archive"
    day = "2026-08-12"
    conn = connect(db)
    try:
        archive_backfill(
            conn,
            pd.DataFrame([_summary_row("MDKA", day)]),
            source_path=tmp_path / "legacy.csv",
            archive_root=archive_root,
        )
        snapshot_id = conn.execute(
            "SELECT broker_snapshot_id FROM broker_snapshots WHERE broker_date=?",
            (day,),
        ).fetchone()[0]
        legacy_manifest = {
            "source": "PORTFOLIO_BACKFILL",
            "daily_only": True,
        }
        conn.execute(
            """
            UPDATE broker_snapshots
            SET from_date='', to_date='', manifest_json=?
            WHERE broker_snapshot_id=?
            """,
            (json.dumps(legacy_manifest), snapshot_id),
        )
        conn.commit()

        assert load_broker_history(conn, "MDKA", day, day) == []
        repaired = repair_legacy_backfill_provenance(conn)
        snapshot = conn.execute(
            "SELECT from_date, to_date, manifest_json FROM broker_snapshots WHERE broker_snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        history = load_broker_history(conn, "MDKA", day, day)
    finally:
        conn.close()

    assert repaired == 1
    assert snapshot[0] == day
    assert snapshot[1] == day
    assert json.loads(snapshot[2])["broker_period_type"] == "1D"
    assert len(history) == 1


def test_unlabeled_legacy_snapshot_is_recovered_from_row_proof(tmp_path):
    db = tmp_path / "history.db"
    archive_root = tmp_path / "archive"
    day = "2026-08-12"
    conn = connect(db)
    try:
        archive_backfill(
            conn,
            pd.DataFrame([_summary_row("MDKA", day)]),
            source_path=tmp_path / "legacy.csv",
            archive_root=archive_root,
        )
        snapshot_id = conn.execute(
            "SELECT broker_snapshot_id FROM broker_snapshots WHERE broker_date=?",
            (day,),
        ).fetchone()[0]
        # Reproduce the oldest form: broker_date and row_json survive, while
        # provenance labels and snapshot period columns are missing.
        conn.execute(
            """
            UPDATE broker_snapshots
            SET from_date='', to_date='', data_quality_status='VALID', manifest_json='{}'
            WHERE broker_snapshot_id=?
            """,
            (snapshot_id,),
        )
        conn.commit()

        assert load_broker_history(conn, "MDKA", day, day) == []
        assert repair_legacy_backfill_provenance(conn) == 1
        history = load_broker_history(conn, "MDKA", day, day)
    finally:
        conn.close()

    assert len(history) == 1
    assert history[0]["broker_date"] == day


def test_row_json_with_multiday_period_is_not_repaired(tmp_path):
    db = tmp_path / "history.db"
    conn = connect(db)
    try:
        from modules.database.swing_history_db import init_schema

        init_schema(conn)
        day = "2026-08-12"
        manifest = {"source": "PORTFOLIO_BACKFILL", "daily_only": True}
        conn.execute(
            """
            INSERT INTO broker_snapshots (
                broker_snapshot_id, broker_date, from_date, to_date, source_files,
                coverage, snapshot_hash, data_quality_status, manifest_json, created_at
            ) VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "row-proves-multiday",
                day,
                "legacy",
                1.0,
                "hash-row",
                "VALID_BACKFILL_DAILY",
                json.dumps(manifest),
                day + "T18:00:00",
            ),
        )
        payload = _summary_row("MDKA", day)
        payload["FROM_DATE"] = "2026-08-11"
        conn.execute(
            "INSERT INTO broker_summary (broker_snapshot_id, symbol, row_json) VALUES (?, ?, ?)",
            ("row-proves-multiday", "MDKA", json.dumps(payload)),
        )
        conn.commit()
        assert repair_legacy_backfill_provenance(conn) == 0
    finally:
        conn.close()


def test_explicit_aggregate_is_not_repaired_even_if_row_dates_match(tmp_path):
    db = tmp_path / "history.db"
    conn = connect(db)
    try:
        from modules.database.swing_history_db import init_schema

        init_schema(conn)
        day = "2026-08-12"
        manifest = {
            "broker_period_type": "AGGREGATE",
            "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
            "aggregate_snapshot": True,
        }
        conn.execute(
            """
            INSERT INTO broker_snapshots (
                broker_snapshot_id, broker_date, from_date, to_date, source_files,
                coverage, snapshot_hash, data_quality_status, manifest_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "explicit-aggregate",
                day,
                day,
                day,
                "legacy",
                1.0,
                "hash-agg",
                "VALID",
                json.dumps(manifest),
                day + "T18:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO broker_summary (broker_snapshot_id, symbol, row_json) VALUES (?, ?, ?)",
            ("explicit-aggregate", "MDKA", json.dumps(_summary_row("MDKA", day))),
        )
        conn.commit()
        assert repair_legacy_backfill_provenance(conn) == 0
    finally:
        conn.close()


def test_position_management_launcher_runs_provenance_repair_first():
    source = (ROOT / "maintenance/RUN_POSITION_MANAGEMENT.bat").read_text(encoding="utf-8-sig")
    repair_call = "repair_legacy_broker_backfill_provenance.py"
    runtime_call = "position_management_runtime.py"
    assert repair_call in source
    assert source.index(repair_call) < source.index(runtime_call)
