from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from modules.analytics.outcome_tracker import (
    canonical_performance_df,
    connect,
    performance_row,
    record_lifecycle_event,
    refresh_lifecycle_integrity_flags,
    register_decision_file,
    update_outcomes,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_signal(
    root: Path,
    *,
    symbol: str = "INDF",
    signal_date: str,
) -> tuple[Path, Path]:
    decisions = root / f"decisions-{signal_date}.csv"
    plans = root / f"plans-{signal_date}.csv"
    pd.DataFrame([{
        "Symbol": symbol,
        "Decision_V3": "BUY CANDIDATE",
        "Technical_Data_Date": signal_date,
        "Final_Score_V3": 80,
        "Data_Quality_Status": "VALID",
        "Setup_Type": "TREND CONTINUATION",
    }]).to_csv(decisions, index=False)
    pd.DataFrame([{
        "Symbol": symbol,
        "Plan_Status": "ACCEPT",
        "Setup_Type": "TREND CONTINUATION",
        "Reference_Close": 7250,
        "Entry_Zone_Low": 7200,
        "Entry_Zone_High": 7300,
        "Initial_Stop": 7000,
        "Target_1": 7500,
        "Target_2": 7800,
        "Max_Hold_Days": 20,
    }]).to_csv(plans, index=False)
    return decisions, plans


def test_historical_recommendation_cannot_mutate_newer_active_lifecycle(tmp_path: Path) -> None:
    conn = connect(tmp_path / "history.db")
    current, plans = _write_signal(tmp_path, signal_date="2026-08-11")
    register_decision_file(conn, current, plans, "RUN-11", "2026-08-11")
    active = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()

    historical, historical_plans = _write_signal(tmp_path, signal_date="2026-08-05")
    result = register_decision_file(
        conn, historical, historical_plans, "BACKFILL-05", "2026-08-05"
    )

    unchanged = conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE signal_id=?", (active["signal_id"],)
    ).fetchone()
    assert result.inserted == 0
    assert result.updated_active == 0
    assert unchanged["signal_date"] == "2026-08-11"
    assert unchanged["latest_scan_date"] == "2026-08-11"
    assert conn.execute(
        "SELECT COUNT(*) FROM signal_recommendation_history WHERE signal_id=?",
        (active["signal_id"],),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM lifecycle_events WHERE signal_id=? AND event_date='2026-08-05'",
        (active["signal_id"],),
    ).fetchone()[0] == 0
    reasons = {
        row[0]
        for row in conn.execute(
            "SELECT reason FROM lifecycle_ingest_quarantine WHERE signal_id=?",
            (active["signal_id"],),
        )
    }
    assert "RECOMMENDATION_BEFORE_SIGNAL_DATE" in reasons
    conn.close()


def test_terminal_reconfirmation_and_invalid_transition_are_quarantined(tmp_path: Path) -> None:
    conn = connect(tmp_path / "history.db")
    decisions, plans = _write_signal(tmp_path, signal_date="2026-08-10")
    register_decision_file(conn, decisions, plans, "RUN-10", "2026-08-10")
    signal_id = conn.execute("SELECT signal_id FROM signal_outcome_ledger").fetchone()[0]
    conn.execute(
        """
        UPDATE signal_outcome_ledger
        SET current_status='CLOSED', entry_date='2026-08-10', exit_date='2026-08-11',
            exit_reason='STOP_LOSS_HIT', final_outcome='LOSS'
        WHERE signal_id=?
        """,
        (signal_id,),
    )

    blocked = record_lifecycle_event(
        conn,
        signal_id=signal_id,
        symbol="INDF",
        event_type="SIGNAL_RECONFIRMED",
        previous_status="CLOSED",
        new_status="CLOSED",
        event_date="2026-08-12",
        event_reason="SCAN:BUY CANDIDATE",
    )
    invalid = record_lifecycle_event(
        conn,
        signal_id="SYNTHETIC",
        symbol="CPRO",
        event_type="STOP_LOSS_HIT",
        previous_status="WAITING_TRIGGER",
        new_status="CLOSED",
        event_date="2026-08-12",
        event_reason="STOP_LOSS_HIT",
    )

    assert blocked == ""
    assert invalid == ""
    assert conn.execute(
        "SELECT COUNT(*) FROM lifecycle_events WHERE signal_id=? AND event_date='2026-08-12'",
        (signal_id,),
    ).fetchone()[0] == 0
    reasons = {row[0] for row in conn.execute("SELECT reason FROM lifecycle_ingest_quarantine")}
    assert "TERMINAL_RECONFIRMATION_REJECTED" in reasons
    assert "INVALID_LIFECYCLE_TRANSITION" in reasons
    conn.close()


def test_direct_baseline_cli_is_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "modules/analytics/outcome_tracker_baseline.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "DIRECT_BASELINE_EXECUTION_DISABLED" in result.stderr
    assert "USE_CANONICAL_OUTCOME_TRACKER" in result.stderr


def test_storage_rejects_duplicate_actionable_symbol_and_migration_fails_closed(
    tmp_path: Path,
) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    decisions, plans = _write_signal(tmp_path, symbol="BBRI", signal_date="2026-08-11")
    register_decision_file(conn, decisions, plans, "RUN-1", "2026-08-11")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO signal_outcome_ledger (
                signal_id, symbol, signal_date, current_status
            ) VALUES ('DUPLICATE-ACTIVE', 'BBRI', '2026-08-12', 'OPEN')
            """
        )
    conn.rollback()

    conn.execute("DROP INDEX uq_signal_ledger_one_actionable_symbol")
    conn.execute(
        """
        INSERT INTO signal_outcome_ledger (
            signal_id, symbol, signal_date, current_status
        ) VALUES ('DUPLICATE-ACTIVE', 'BBRI', '2026-08-12', 'OPEN')
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="DUPLICATE_ACTIONABLE_LIFECYCLE:BBRI"):
        connect(db)


def test_same_session_reentry_is_suppressed_and_next_session_has_lineage(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "history.db")
    original, plans = _write_signal(tmp_path, symbol="BRPT", signal_date="2026-08-10")
    register_decision_file(conn, original, plans, "RUN-10", "2026-08-10")
    parent = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    conn.execute(
        """
        UPDATE signal_outcome_ledger
        SET current_status='CLOSED', entry_date='2026-08-10', entry_price=7250,
            exit_date='2026-08-11', exit_price=7000,
            exit_reason='STOP_LOSS_HIT', final_outcome='LOSS'
        WHERE signal_id=?
        """,
        (parent["signal_id"],),
    )

    same_day, same_day_plans = _write_signal(
        tmp_path, symbol="BRPT", signal_date="2026-08-11"
    )
    suppressed = register_decision_file(
        conn, same_day, same_day_plans, "RUN-11", "2026-08-11"
    )
    assert suppressed.inserted == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM signal_outcome_ledger WHERE current_status IN ('WAITING_TRIGGER','OPEN')"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM lifecycle_ingest_quarantine "
        "WHERE reason='REENTRY_SAME_SESSION_SUPPRESSED'"
    ).fetchone()[0] == 1

    next_day, next_day_plans = _write_signal(
        tmp_path, symbol="BRPT", signal_date="2026-08-12"
    )
    created = register_decision_file(
        conn, next_day, next_day_plans, "RUN-12", "2026-08-12"
    )
    assert created.inserted == 1
    child = conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE current_status='WAITING_TRIGGER'"
    ).fetchone()
    assert child["signal_id"] != parent["signal_id"]
    assert child["parent_signal_id"] == parent["signal_id"]
    assert child["supersedes_signal_id"] == parent["signal_id"]
    assert child["reentry_reason"] == "AFTER_STOP_LOSS"
    conn.close()


def test_same_day_entry_and_stop_remains_valid_within_one_lifecycle(tmp_path: Path) -> None:
    historical = tmp_path / "prices"
    historical.mkdir()
    pd.DataFrame([{
        "Date": "2026-08-11",
        "Open": 7250,
        "High": 7300,
        "Low": 6900,
        "Close": 7000,
        "Volume": 1000,
    }]).to_csv(historical / "CPRO.csv", index=False)
    conn = connect(tmp_path / "history.db")
    decisions, plans = _write_signal(tmp_path, symbol="CPRO", signal_date="2026-08-10")
    register_decision_file(conn, decisions, plans, "RUN-10", "2026-08-10")
    update_outcomes(conn, historical)

    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["entry_date"] == "2026-08-11"
    assert row["exit_date"] == "2026-08-11"
    assert row["current_status"] == "CLOSED"
    assert row["exit_reason"] == "STOP_LOSS_HIT"
    transitions = {
        (event["event_type"], event["previous_status"], event["new_status"])
        for event in conn.execute(
            "SELECT event_type,previous_status,new_status FROM lifecycle_events"
        )
    }
    assert ("ENTRY_TRIGGERED", "WAITING_TRIGGER", "OPEN") in transitions
    assert ("STOP_LOSS_HIT", "OPEN", "CLOSED") in transitions
    conn.close()


def test_legacy_tp1_terminal_is_flagged_and_excluded_without_rewriting_raw_row(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "history.db")
    conn.execute(
        """
        INSERT INTO signal_outcome_ledger (
            signal_id, symbol, signal_date, raw_decision, data_quality_status,
            current_status, entry_date, entry_price, exit_date, exit_price,
            exit_reason, final_outcome, realized_return_pct, holding_days,
            tp1_hit, tp2_hit, trailing_active
        ) VALUES (
            'LEGACY-TP1', 'PNLF', '2026-08-10', 'BUY CANDIDATE', 'VALID',
            'CLOSED', '2026-08-11', 228, '2026-08-13', 237,
            'TP1_HIT', 'WIN', 3.9, 3, 1, 0, 0
        )
        """
    )
    raw_before = dict(conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE signal_id='LEGACY-TP1'"
    ).fetchone())

    assert refresh_lifecycle_integrity_flags(conn) >= 1
    flag = conn.execute(
        "SELECT * FROM lifecycle_integrity_flags WHERE signal_id='LEGACY-TP1'"
    ).fetchone()
    assert flag["reason"] == "LEGACY_TP1_TERMINAL_QUARANTINE"
    assert flag["canonical_eligible"] == 0
    assert canonical_performance_df(conn).empty
    raw_after = dict(conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE signal_id='LEGACY-TP1'"
    ).fetchone())
    assert raw_after == raw_before
    conn.close()


def test_negative_return_win_is_generically_quarantined(tmp_path: Path) -> None:
    conn = connect(tmp_path / "history.db")
    conn.execute(
        """
        INSERT INTO signal_outcome_ledger (
            signal_id, symbol, signal_date, raw_decision, data_quality_status,
            current_status, entry_date, exit_date, exit_reason, final_outcome,
            realized_return_pct, tp1_hit, tp2_hit
        ) VALUES (
            'NEGATIVE-WIN', 'MDKA', '2026-08-04', 'BUY CANDIDATE', 'VALID',
            'CLOSED', '2026-08-10', '2026-08-11', 'TP1_HIT', 'WIN',
            -0.09, 1, 0
        )
        """
    )
    refresh_lifecycle_integrity_flags(conn)
    reasons = {
        row[0]
        for row in conn.execute(
            "SELECT reason FROM lifecycle_integrity_flags WHERE signal_id='NEGATIVE-WIN'"
        )
    }
    assert "LEGACY_TP1_TERMINAL_QUARANTINE" in reasons
    assert "NON_POSITIVE_RETURN_WIN_QUARANTINE" in reasons
    assert canonical_performance_df(conn).empty
    conn.close()


def test_current_recommendations_counts_only_active_and_preserves_episode_count() -> None:
    frame = pd.DataFrame([
        {
            "current_status": "OPEN",
            "raw_decision": "BUY CANDIDATE",
            "data_quality_status": "VALID",
            "entry_date": "2026-08-11",
            "final_outcome": "OPEN",
            "realized_return_pct": None,
            "tp1_hit": 0,
            "tp2_hit": 0,
            "sl_hit": 0,
            "holding_days": 1,
            "mfe_pct": 1,
            "mae_pct": -1,
        },
        {
            "current_status": "WAITING_TRIGGER",
            "raw_decision": "BUY CANDIDATE",
            "data_quality_status": "VALID",
            "entry_date": None,
            "final_outcome": None,
            "realized_return_pct": None,
            "tp1_hit": 0,
            "tp2_hit": 0,
            "sl_hit": 0,
            "holding_days": None,
            "mfe_pct": None,
            "mae_pct": None,
        },
        {
            "current_status": "CLOSED",
            "raw_decision": "BUY CANDIDATE",
            "data_quality_status": "VALID",
            "entry_date": "2026-08-01",
            "final_outcome": "WIN",
            "realized_return_pct": 5,
            "tp1_hit": 1,
            "tp2_hit": 1,
            "sl_hit": 0,
            "holding_days": 3,
            "mfe_pct": 6,
            "mae_pct": -1,
        },
    ])
    summary = performance_row(frame, "ALL")
    assert summary["Current_Recommendations"] == 2
    assert summary["Historical_Signal_Episodes"] == 3
    assert summary["Closed"] == 1
