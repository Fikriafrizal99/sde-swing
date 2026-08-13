from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from modules.analytics.outcome_tracker import (
    connect,
    record_lifecycle_event,
    register_decision_file,
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
