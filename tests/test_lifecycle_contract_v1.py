from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.analytics.lifecycle_contract import (
    LIFECYCLE_CONTRACT_VERSION,
    evaluate_trade_path,
)
from modules.analytics.outcome_tracker import (
    connect,
    pending_lifecycle_events,
    register_decision_file,
    update_outcomes,
)
from modules.analytics.profile_shadow import comparison_metrics
from modules.backtesting.backtest_engine import evaluate_signal
from modules.exit_engine.exit_engine import update_active_trade


def _bars(rows):
    frame = pd.DataFrame(rows)
    frame["Date"] = pd.to_datetime(frame["Date"])
    if "EMA20" not in frame:
        frame["EMA20"] = frame["Close"]
    if "ATR14" not in frame:
        frame["ATR14"] = 2.0
    return frame


def test_tp1_is_milestone_and_tp2_is_full_close():
    bars = _bars([
        {"Date": "2026-08-04", "Open": 100, "High": 111, "Low": 99, "Close": 108},
    ])
    state = evaluate_trade_path(
        bars,
        entry_price=100,
        initial_stop=95,
        target_1=110,
        target_2=115,
        max_hold_days=20,
    )
    assert state.current_status == "OPEN"
    assert state.final_outcome == "OPEN"
    assert state.tp1_hit is True
    assert state.trailing_active is True
    assert [event["event_type"] for event in state.events] == ["TP1_HIT"]

    continued = evaluate_trade_path(
        _bars([{"Date": "2026-08-05", "Open": 112, "High": 116, "Low": 108, "Close": 115}]),
        entry_price=100,
        initial_stop=95,
        target_1=110,
        target_2=115,
        max_hold_days=20,
        current_stop=state.current_stop,
        holding_days=state.holding_days,
        tp1_hit=state.tp1_hit,
        trailing_active=state.trailing_active,
        tp1_hit_date=state.tp1_hit_date,
        max_price=state.max_price,
        min_price=state.min_price,
        last_evaluated_date=state.last_evaluated_date,
    )
    assert continued.current_status == "CLOSED"
    assert continued.tp2_hit is True
    assert continued.exit_reason == "TP2_HIT"
    assert continued.final_outcome == "WIN"


def test_same_candle_stop_has_conservative_priority():
    state = evaluate_trade_path(
        _bars([{"Date": "2026-08-04", "Open": 100, "High": 116, "Low": 94, "Close": 110}]),
        entry_price=100,
        initial_stop=95,
        target_1=110,
        target_2=115,
        max_hold_days=20,
    )
    assert state.current_status == "CLOSED"
    assert state.exit_price == 95
    assert state.exit_reason == "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE"
    assert state.final_outcome == "LOSS"


def test_max_hold_uses_executable_holding_sessions():
    state = evaluate_trade_path(
        _bars([
            {"Date": "2026-08-04", "Open": 100, "High": 105, "Low": 99, "Close": 103},
            {"Date": "2026-08-05", "Open": 103, "High": 106, "Low": 100, "Close": 104},
        ]),
        entry_price=100,
        initial_stop=95,
        target_1=110,
        target_2=115,
        max_hold_days=2,
    )
    assert state.current_status == "CLOSED"
    assert state.holding_days == 2
    assert state.exit_reason == "MAX_HOLD_EXIT"


def _signal_files(root: Path) -> tuple[Path, Path]:
    decisions = root / "FINAL_DECISION_V3.csv"
    plans = root / "ENTRY_PLANS.csv"
    pd.DataFrame([{
        "Symbol": "BBCA",
        "Decision_V3": "BUY",
        "Technical_Data_Date": "2026-08-01",
        "Final_Score_V3": 82,
        "Data_Quality_Status": "VALID",
        "Setup_Type": "BREAKOUT",
    }]).to_csv(decisions, index=False)
    pd.DataFrame([{
        "Symbol": "BBCA",
        "Plan_Status": "ACCEPT",
        "Setup_Type": "BREAKOUT",
        "Reference_Close": 103,
        "Entry_Zone_Low": 100,
        "Entry_Zone_High": 105,
        "Initial_Stop": 95,
        "Target_1": 110,
        "Target_2": 115,
        "Max_Hold_Days": 20,
    }]).to_csv(plans, index=False)
    return decisions, plans


def _prices(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_outcome_tracker_keeps_tp1_open_then_closes_at_tp2(tmp_path: Path):
    db = tmp_path / "history.db"
    historical = tmp_path / "prices"
    historical.mkdir()
    decisions, plans = _signal_files(tmp_path)
    price_path = historical / "BBCA.csv"

    _prices(price_path, [
        {"Date": "2026-08-04", "Open": 103, "High": 106, "Low": 101, "Close": 104},
    ])
    conn = connect(db)
    register_decision_file(conn, decisions, plans, "RUN-1", "2026-08-01")
    update_outcomes(conn, historical)
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "OPEN"
    assert row["entry_price"] == 103

    _prices(price_path, [
        {"Date": "2026-08-04", "Open": 103, "High": 106, "Low": 101, "Close": 104},
        {"Date": "2026-08-05", "Open": 106, "High": 111, "Low": 105, "Close": 109},
    ])
    update_outcomes(conn, historical)
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "OPEN"
    assert row["final_outcome"] == "OPEN"
    assert row["tp1_hit"] == 1
    assert row["trailing_active"] == 1
    assert row["exit_price"] is None
    assert "TP1_HIT" in {event["event_type"] for event in pending_lifecycle_events(conn)}

    _prices(price_path, [
        {"Date": "2026-08-04", "Open": 103, "High": 106, "Low": 101, "Close": 104},
        {"Date": "2026-08-05", "Open": 106, "High": 111, "Low": 105, "Close": 109},
        {"Date": "2026-08-06", "Open": 112, "High": 116, "Low": 108, "Close": 115},
    ])
    update_outcomes(conn, historical)
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "CLOSED"
    assert row["tp1_hit"] == 1
    assert row["tp2_hit"] == 1
    assert row["exit_reason"] == "TP2_HIT"
    assert row["final_outcome"] == "WIN"
    conn.close()


def test_exit_engine_persists_tp1_trailing_state_without_closing():
    trade = pd.Series({
        "Symbol": "BBCA",
        "Entry_Price": 100,
        "Initial_Stop": 95,
        "Current_Stop": 95,
        "Target_1": 110,
        "Target_2": 115,
        "Highest_Close": 100,
        "Holding_Days": 0,
        "Status": "ACTIVE",
    })
    px = _bars([
        {"Date": "2026-08-04", "Open": 100, "High": 111, "Low": 99, "Close": 108, "EMA20": 103, "ATR14": 2},
    ])
    updated, alert = update_active_trade(trade, None, px, 20)
    assert alert is None
    assert updated["Status"] == "ACTIVE"
    assert updated["TP1_Hit"] is True
    assert updated["Trailing_Active"] is True
    assert updated["Lifecycle_Contract_Version"] == LIFECYCLE_CONTRACT_VERSION


def test_shadow_metrics_use_explicit_canonical_hit_columns():
    shadow = pd.DataFrame([{
        "Symbol": "BBCA",
        "Moderate_Profile": "MODERATE_BASELINE",
        "Decision_Status_PrePlan": "BUY ON TRIGGER",
        "Decision_Status_Final": "BUY ON TRIGGER",
        "Final_Score": 70,
        "Setup_Type": "BREAKOUT",
        "Market_Regime": "BULLISH",
        "Estimated_Slippage_Pct": 0.1,
        "Position_Size_Multiplier": 1.0,
    }])
    outcomes = pd.DataFrame([{
        "Symbol": "BBCA",
        "Final_Outcome": "OPEN",
        "TP1_Hit": True,
        "TP2_Hit": False,
        "SL_Hit": False,
        "Return_R": 0.5,
    }])
    metrics = comparison_metrics(shadow, outcomes).iloc[0]
    assert metrics["Target_1_Rate"] == 1.0
    assert metrics["Target_2_Rate"] == 0.0
    assert metrics["Stop_Rate"] == 0.0


def test_backtest_plan_uses_actual_trigger_entry_and_tp1_does_not_close():
    signal = pd.Series({
        "Signal_Date": pd.Timestamp("2026-08-01"),
        "Symbol": "BBCA",
        "Gated_Decision": "BUY ON TRIGGER",
        "Plan_Status": "ACCEPT",
        "Setup_Type": "BREAKOUT",
        "Entry_Zone_Low": 100,
        "Entry_Zone_High": 105,
        "Reference_Close": 99,
        "Initial_Stop": 95,
        "Target_1": 110,
        "Target_2": 120,
        "Max_Hold_Days": 20,
        "Estimated_Slippage_Pct": 0,
    })
    px = _bars([
        {"Date": "2026-08-04", "Open": 103, "High": 106, "Low": 101, "Close": 104},
        {"Date": "2026-08-05", "Open": 106, "High": 111, "Low": 105, "Close": 109},
    ])
    result = evaluate_signal(signal, px, [1], "next_open", 20)
    assert result["Entry_Price"] == 103
    assert result["Entry_Price"] != signal["Reference_Close"]
    assert result["TP1_Hit"] is True
    assert result["Final_Outcome"] == "OPEN"
