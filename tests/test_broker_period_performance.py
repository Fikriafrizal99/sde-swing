from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.broker_period_performance import build_reports, capture


LEDGER_SCHEMA = """
CREATE TABLE signal_outcome_ledger (
    signal_id TEXT PRIMARY KEY,
    run_id TEXT,
    latest_scan_run_id TEXT,
    symbol TEXT,
    signal_date TEXT,
    created_at TEXT,
    broker_confidence REAL,
    broker_confidence_bucket TEXT,
    broker_direction TEXT,
    source_json TEXT,
    data_quality_status TEXT,
    current_status TEXT,
    final_outcome TEXT,
    entry_date TEXT,
    realized_return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL
);
"""


def _insert_signal(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    signal_date: str,
    created_at: str,
    run_id: str,
    latest_run: str,
    confidence: float,
) -> None:
    source = {
        "decision": {
            "Broker_Score_Final": 77,
            "Broker_Confidence_Final": confidence,
        }
    }
    conn.execute(
        """
        INSERT INTO signal_outcome_ledger (
            signal_id, run_id, latest_scan_run_id, symbol, signal_date, created_at,
            broker_confidence, broker_confidence_bucket, broker_direction,
            source_json, data_quality_status, current_status, final_outcome,
            entry_date, realized_return_pct, mfe_pct, mae_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            run_id,
            latest_run,
            "BBCA" if signal_id == "NEW" else "TLKM",
            signal_date,
            created_at,
            confidence,
            ">=75%" if confidence >= 75 else "60-74%",
            "ACCUMULATION",
            json.dumps(source),
            "VALID",
            "WAITING_TRIGGER",
            None,
            None,
            None,
            None,
            None,
        ),
    )


def test_period_capture_is_additive_and_old_signal_is_rescan_only(tmp_path: Path):
    db = tmp_path / "sde.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEDGER_SCHEMA)
    _insert_signal(
        conn,
        signal_id="OLD",
        signal_date="2026-08-07",
        created_at="2026-08-07T18:05:00+07:00",
        run_id="CURRENT-RUN",  # active ledger run_id may be refreshed by tracker
        latest_run="CURRENT-RUN",
        confidence=82,
    )
    _insert_signal(
        conn,
        signal_id="NEW",
        signal_date="2026-08-10",
        created_at="2026-08-10T18:05:00+07:00",
        run_id="CURRENT-RUN",
        latest_run="CURRENT-RUN",
        confidence=80,
    )
    conn.commit()
    before_count = conn.execute("SELECT COUNT(*) FROM signal_outcome_ledger").fetchone()[0]
    conn.close()

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({
            "snapshot_id": "BROKER-3D-TEST",
            "created_at": "2026-08-10T18:00:00+07:00",
            "broker_period_type": "3D",
            "broker_period_start": "2026-08-06",
            "broker_period_end": "2026-08-10",
            "broker_trading_days": 3,
            "freshness_status": "CURRENT",
            "scoring_adjustment_applied": False,
            "freshness_adjustment_applied": False,
            "persistence_adjustment_applied": False,
        }),
        encoding="utf-8",
    )

    assert capture(db, "CURRENT-RUN", manifest) == 0

    conn = sqlite3.connect(db)
    after_count = conn.execute("SELECT COUNT(*) FROM signal_outcome_ledger").fetchone()[0]
    roles = dict(
        conn.execute(
            "SELECT signal_id, context_role FROM broker_period_signal_context ORDER BY signal_id"
        ).fetchall()
    )
    conn.close()

    # Existing production ledger rows are not inserted/updated/deleted.
    assert before_count == 2
    assert after_count == before_count
    assert roles == {"NEW": "INITIAL_SIGNAL", "OLD": "RESCAN_CONTEXT"}

    period, cross, coverage = build_reports(db, tmp_path / "reports")
    assert coverage["ledger_signals"] == 2
    assert coverage["mapped_initial_signals"] == 1
    assert coverage["rescan_context_rows"] == 1
    assert period["Broker_Period"].tolist() == ["3D"]
    assert int(period.iloc[0]["Signals"]) == 1
    assert cross["Broker_Confidence_Bucket"].tolist() == [">=75%"]
