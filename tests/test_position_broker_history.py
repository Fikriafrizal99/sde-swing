from __future__ import annotations

import json
import sqlite3

import pandas as pd

from modules.broker_fusion.broker_fusion import fuse
from modules.portfolio.broker_history_context import (
    build_position_broker_context,
    ensure_schema,
    persist_position_broker_context,
    sync_latest_broker_summary,
)


def _payload(symbol: str, direction: str) -> dict:
    if direction == "ACCUMULATION":
        return {
            "EMITEN": symbol,
            "TOTAL_BUY": 150.0,
            "TOTAL_SELL": 50.0,
            "NET_FLOW": 100.0,
            "TOTAL_VALUE": 200.0,
            "TOTAL_VOLUME": 1000.0,
            "BUYER_CONCENTRATION": 0.70,
            "SELLER_CONCENTRATION": 0.30,
            "BROKER_ACCDIST": "BIG ACC",
            "AVG_ACCDIST": "BIG ACC",
            "TOP3_ACCDIST": "BIG ACC",
        }
    if direction == "DISTRIBUTION":
        return {
            "EMITEN": symbol,
            "TOTAL_BUY": 50.0,
            "TOTAL_SELL": 150.0,
            "NET_FLOW": -100.0,
            "TOTAL_VALUE": 200.0,
            "TOTAL_VOLUME": 1000.0,
            "BUYER_CONCENTRATION": 0.30,
            "SELLER_CONCENTRATION": 0.70,
            "BROKER_ACCDIST": "BIG DIST",
            "AVG_ACCDIST": "BIG DIST",
            "TOP3_ACCDIST": "BIG DIST",
        }
    return {
        "EMITEN": symbol,
        "TOTAL_BUY": 100.0,
        "TOTAL_SELL": 100.0,
        "NET_FLOW": 0.0,
        "TOTAL_VALUE": 200.0,
        "TOTAL_VOLUME": 1000.0,
        "BUYER_CONCENTRATION": 0.50,
        "SELLER_CONCENTRATION": 0.50,
        "BROKER_ACCDIST": "NEUTRAL",
        "AVG_ACCDIST": "NEUTRAL",
        "TOP3_ACCDIST": "NEUTRAL",
    }


def _insert_snapshot(conn: sqlite3.Connection, broker_date: str, symbol: str, payload: dict) -> None:
    snapshot_id = f"snap-{broker_date}"
    conn.execute(
        """
        INSERT INTO broker_snapshots (
            broker_snapshot_id, broker_date, from_date, to_date, source_files,
            coverage, snapshot_hash, data_quality_status, manifest_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            broker_date,
            broker_date,
            broker_date,
            "fixture.csv",
            1.0,
            snapshot_id,
            "VALID",
            "{}",
            broker_date + "T18:00:00+07:00",
        ),
    )
    conn.execute(
        "INSERT INTO broker_summary (broker_snapshot_id, symbol, row_json) VALUES (?, ?, ?)",
        (snapshot_id, symbol, json.dumps(payload)),
    )


def test_position_broker_history_uses_current_3d_5d_7d_and_since_entry(tmp_path):
    db = tmp_path / "history.db"
    conn = sqlite3.connect(db)
    try:
        ensure_schema(conn)
        sequence = [
            ("2026-08-03", "ACCUMULATION"),
            ("2026-08-04", "ACCUMULATION"),
            ("2026-08-05", "ACCUMULATION"),
            ("2026-08-06", "NEUTRAL"),
            ("2026-08-07", "DISTRIBUTION"),
        ]
        for broker_date, direction in sequence:
            _insert_snapshot(conn, broker_date, "TINS", _payload("TINS", direction))
        conn.commit()

        context = build_position_broker_context(
            conn,
            symbol="TINS",
            buy_date="2026-08-03",
            analysis_date="2026-08-07",
        )

        assert context["observation_count"] == 5
        assert context["current_state"] == "DISTRIBUTION"
        assert context["3D"]["context"] == "NEUTRAL"
        assert context["5D"]["context"] == "ACCUMULATION"
        assert context["7D"]["context"] == "ACCUMULATION"
        assert context["since_entry"]["context"] == "ACCUMULATION"
        assert context["since_entry"]["net_flow"] > 0
        assert context["effective_state"] == "NEUTRAL"
        assert "conflicts" in context["effective_reason"]

        persist_position_broker_context(conn, position_id="pos-tins", context=context)
        stored = conn.execute(
            "SELECT current_state, effective_state, context_5d, context_since_entry "
            "FROM position_broker_context_history WHERE position_id='pos-tins'"
        ).fetchone()
        assert stored == ("DISTRIBUTION", "NEUTRAL", "ACCUMULATION", "ACCUMULATION")
    finally:
        conn.close()


def test_latest_broker_summary_archives_candidate_and_open_portfolio_into_same_db(tmp_path):
    db = tmp_path / "history.db"
    broker_csv = tmp_path / "BROKER_SUMMARY_LATEST.csv"
    rows = []
    for symbol in ("BBCA", "TINS"):
        row = _payload(symbol, "ACCUMULATION")
        row["TO_DATE"] = "2026-08-07"
        rows.append(row)
    pd.DataFrame(rows).to_csv(broker_csv, index=False)

    conn = sqlite3.connect(db)
    try:
        ensure_schema(conn)
        snapshot_id = sync_latest_broker_summary(conn, broker_csv)
        assert snapshot_id
        symbols = {
            row[0]
            for row in conn.execute(
                "SELECT symbol FROM broker_summary WHERE broker_snapshot_id=?",
                (snapshot_id,),
            ).fetchall()
        }
        assert symbols == {"BBCA", "TINS"}
        assert conn.execute(
            "SELECT COUNT(*) FROM broker_snapshots WHERE broker_snapshot_id=?",
            (snapshot_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_extra_portfolio_broker_row_cannot_expand_final_watchlist_candidate_universe(tmp_path):
    technical_csv = tmp_path / "technical_candidates_top40.csv"
    broker_csv = tmp_path / "BROKER_SUMMARY_LATEST.csv"
    output_csv = tmp_path / "FINAL_DECISION_V2.csv"

    pd.DataFrame(
        [
            {"Symbol": "BBCA", "Technical_Score_Final": 80.0},
            {"Symbol": "ANTM", "Technical_Score_Final": 75.0},
        ]
    ).to_csv(technical_csv, index=False)

    broker_rows = []
    for symbol in ("BBCA", "ANTM", "TINS"):
        row = _payload(symbol, "ACCUMULATION")
        row["TO_DATE"] = "2026-08-07"
        broker_rows.append(row)
    pd.DataFrame(broker_rows).to_csv(broker_csv, index=False)

    result = fuse(
        technical_csv,
        broker_csv,
        output_csv,
        run_id="portfolio-isolation-test",
        min_coverage=1.0,
        expected_broker_date="2026-08-07",
    )

    # TINS represents an OPEN portfolio symbol appended to the Tampermonkey
    # broker CSV.  Broker Fusion must still be candidate-left and cannot make
    # TINS a Final Watchlist candidate by itself.
    assert set(result["Symbol"]) == {"BBCA", "ANTM"}
    assert len(result) == 2
    assert "TINS" not in set(pd.read_csv(output_csv)["Symbol"])
