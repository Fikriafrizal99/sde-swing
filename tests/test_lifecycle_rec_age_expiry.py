from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from modules.analytics.outcome_tracker import (
    active_recommendations_df,
    connect,
    lifecycle_age_sessions,
    pending_lifecycle_events,
    register_decision_file,
    recommendation_count,
    update_outcomes,
)
from modules.analytics.outcome_tracker import _status_changes_telegram


def _write_signal_files(
    root: Path,
    *,
    signal_date: str,
    entry_low: float = 100,
    entry_high: float = 105,
    stop: float = 95,
    tp1: float = 110,
    tp2: float = 115,
    decision: str = "BUY",
    plan_status: str = "ACCEPT",
    rejection_reason: str = "",
    minor_resistance: float | None = None,
) -> tuple[Path, Path]:
    decisions = root / "FINAL_DECISION_V3.csv"
    plans = root / "ENTRY_PLANS.csv"
    decision_row = {
        "Symbol": "BBCA",
        "Decision_V3": decision,
        "Technical_Data_Date": signal_date,
        "Final_Score_V3": 82,
        "Technical_Score_Final": 77,
        "Broker_Confidence_Final": 71,
        "Data_Quality_Status": "VALID",
        "Setup_Type": "BREAKOUT",
    }
    pd.DataFrame([decision_row]).to_csv(decisions, index=False)
    plan = {
        "Symbol": "BBCA",
        "Plan_Status": plan_status,
        "Setup_Type": "BREAKOUT",
        "Reference_Close": entry_low,
        "Entry_Zone_Low": entry_low,
        "Entry_Zone_High": entry_high,
        "Initial_Stop": stop,
        "Target_1": tp1,
        "Target_2": tp2,
        "Max_Hold_Days": 20,
    }
    if rejection_reason:
        plan["Rejection_Reason"] = rejection_reason
    if minor_resistance is not None:
        plan["Minor_Resistance"] = minor_resistance
    pd.DataFrame([plan]).to_csv(plans, index=False)
    return decisions, plans


def _register(
    conn: sqlite3.Connection,
    root: Path,
    signal_date: str,
    run_id: str,
    **kwargs,
):
    decisions, plans = _write_signal_files(root, signal_date=signal_date, **kwargs)
    return register_decision_file(conn, decisions, plans, run_id, signal_date)


def _empty_scan(root: Path) -> Path:
    path = root / "EMPTY_DECISION.csv"
    pd.DataFrame(columns=["Symbol", "Decision_V3"]).to_csv(path, index=False)
    return path


def test_rec_counts_unique_recommendation_sessions_and_missing_scan_does_not_count(tmp_path: Path) -> None:
    conn = connect(tmp_path / "history.db")
    _register(conn, tmp_path, "2026-08-12", "RUN-12")
    _register(conn, tmp_path, "2026-08-12", "RUN-12-RERUN")
    assert conn.execute("SELECT COUNT(*) FROM signal_recommendation_history").fetchone()[0] == 1

    _register(conn, tmp_path, "2026-08-13", "RUN-13")
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert recommendation_count(conn, row["signal_id"]) == 2
    assert row["signal_date"] == "2026-08-12"
    assert row["score"] == 82
    assert row["broker_confidence"] == 71
    assert row["stop_loss"] == 95
    assert row["take_profit_1"] == 110
    assert row["take_profit_2"] == 115

    empty = _empty_scan(tmp_path)
    register_decision_file(conn, empty, tmp_path / "ENTRY_PLANS.csv", "RUN-14", "2026-08-14")
    assert recommendation_count(conn, row["signal_id"]) == 2
    conn.close()


def test_age_uses_exact_idx_sessions_and_signal_date_is_age_zero(tmp_path: Path) -> None:
    assert lifecycle_age_sessions("2026-08-12", "2026-08-12") == 0
    assert lifecycle_age_sessions("2026-08-12", "2026-08-13") == 1
    # 15–16 Aug is weekend and 17 Aug is an IDX holiday in the configured
    # calendar, so Tuesday 18 Aug is only the next elapsed session.
    assert lifecycle_age_sessions("2026-08-14", "2026-08-18") == 1


def test_waiting_expires_at_age_seven_and_retains_rec_history(tmp_path: Path) -> None:
    conn = connect(tmp_path / "history.db")
    for index, day in enumerate(("2026-08-12", "2026-08-13", "2026-08-14", "2026-08-18", "2026-08-19", "2026-08-20"), 1):
        _register(conn, tmp_path, day, f"RUN-{index}")
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert recommendation_count(conn, row["signal_id"]) == 6
    empty = _empty_scan(tmp_path)
    register_decision_file(conn, empty, tmp_path / "ENTRY_PLANS.csv", "RUN-AGE-6", "2026-08-20")
    assert conn.execute("SELECT current_status FROM signal_outcome_ledger").fetchone()[0] == "WAITING_TRIGGER"
    register_decision_file(conn, empty, tmp_path / "ENTRY_PLANS.csv", "RUN-AGE-7", "2026-08-24")
    expired = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert expired["current_status"] == "EXPIRED"
    assert expired["exit_reason"] == "TRIGGER_NOT_REACHED_WITHIN_WINDOW"
    assert recommendation_count(conn, expired["signal_id"]) == 6
    assert active_recommendations_df(conn, tmp_path / "prices").empty
    expired_event = next(event for event in pending_lifecycle_events(conn) if event["event_type"] == "EXPIRED")
    message = _status_changes_telegram([expired_event])
    assert "Waiting 7 sesi perdagangan" in message
    assert "REC selama lifecycle: 6x" in message
    assert "Original signal: 12 Aug 2026" in message
    conn.close()


def test_trigger_on_final_waiting_session_becomes_open_and_open_ignores_waiting_expiry(tmp_path: Path) -> None:
    historical = tmp_path / "prices"
    historical.mkdir()
    pd.DataFrame({
        "Date": pd.to_datetime(["2026-08-13", "2026-08-14", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]),
        "Open": [103] * 6,
        "High": [104, 104, 104, 104, 104, 106],
        "Low": [101] * 6,
        "Close": [103] * 5 + [106],
    }).to_csv(historical / "BBCA.csv", index=False)
    conn = connect(tmp_path / "history.db")
    decisions, plans = _write_signal_files(
        tmp_path,
        signal_date="2026-08-12",
        plan_status="CONDITIONAL",
        rejection_reason="MINOR_RESISTANCE_NEAR",
        minor_resistance=105,
    )
    register_decision_file(
        conn,
        decisions,
        plans,
        "RUN-1",
        "2026-08-12",
        historical_dir=historical,
    )
    decisions, plans = _write_signal_files(tmp_path, signal_date="2026-08-21")
    register_decision_file(
        conn,
        decisions,
        plans,
        "RUN-8",
        "2026-08-21",
        historical_dir=historical,
    )
    opened = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert opened["current_status"] == "OPEN"
    conn.close()


def test_invalidated_before_entry_is_not_expired(tmp_path: Path) -> None:
    conn = connect(tmp_path / "history.db")
    _register(conn, tmp_path, "2026-08-12", "RUN-INVALID", plan_status="REJECT")
    empty = _empty_scan(tmp_path)
    register_decision_file(conn, empty, tmp_path / "ENTRY_PLANS.csv", "RUN-21", "2026-08-24")
    assert conn.execute("SELECT current_status FROM signal_outcome_ledger").fetchone()[0] == "INVALIDATED_BEFORE_ENTRY"
    conn.close()


def test_expiry_day_suppresses_reentry_until_next_session(tmp_path: Path) -> None:
    conn = connect(tmp_path / "history.db")
    for index, day in enumerate(("2026-08-12", "2026-08-13", "2026-08-14", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"), 1):
        _register(conn, tmp_path, day, f"RUN-OLD-{index}")
    suppressed = _register(
        conn,
        tmp_path,
        "2026-08-24",
        "RUN-SAME-SESSION",
        entry_low=200,
        entry_high=205,
        stop=190,
        tp1=220,
        tp2=230,
    )
    assert suppressed.inserted == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM signal_outcome_ledger WHERE current_status IN ('WAITING_TRIGGER','OPEN')"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM lifecycle_ingest_quarantine "
        "WHERE reason='REENTRY_SAME_SESSION_SUPPRESSED'"
    ).fetchone()[0] == 1

    _register(
        conn,
        tmp_path,
        "2026-08-26",
        "RUN-NEW",
        entry_low=200,
        entry_high=205,
        stop=190,
        tp1=220,
        tp2=230,
    )
    rows = conn.execute("SELECT * FROM signal_outcome_ledger ORDER BY signal_date").fetchall()
    assert len(rows) == 2
    old, new = rows
    assert old["current_status"] == "EXPIRED"
    assert new["current_status"] == "WAITING_TRIGGER"
    assert old["signal_id"] != new["signal_id"]
    assert recommendation_count(conn, old["signal_id"]) == 7
    assert recommendation_count(conn, new["signal_id"]) == 1
    assert new["signal_date"] == "2026-08-26"
    assert new["parent_signal_id"] == old["signal_id"]
    assert new["supersedes_signal_id"] == old["signal_id"]
    assert new["reentry_reason"] == "AFTER_EXPIRED"
    assert new["entry_zone_low"] == 200
    assert new["stop_loss"] == 190
    active = active_recommendations_df(conn, tmp_path / "prices")
    assert len(active) == 1
    assert active.iloc[0]["signal_id"] == new["signal_id"]
    assert active.iloc[0]["recommendation_count"] == 1
    assert active.iloc[0]["age_sessions"] == 0
    conn.close()
