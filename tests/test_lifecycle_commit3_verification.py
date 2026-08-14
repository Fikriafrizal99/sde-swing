from __future__ import annotations

import pandas as pd

from modules.backtesting.backtest_engine import evaluate_signal
from modules.exit_engine.exit_engine import update_active_trade


def _px(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["Date"] = pd.to_datetime(frame["Date"])
    if "EMA20" not in frame:
        frame["EMA20"] = frame["Close"]
    if "ATR14" not in frame:
        frame["ATR14"] = 2.0
    return frame


def test_exit_rerun_same_session_does_not_increment_holding_days_twice():
    trade = pd.Series({
        "Symbol": "BBCA",
        "Entry_Price": 100,
        "Initial_Stop": 95,
        "Current_Stop": 95,
        "Target_1": 110,
        "Target_2": 120,
        "Highest_Close": 103,
        "Holding_Days": 3,
        "Status": "ACTIVE",
        "Last_Update": "2026-08-10",
    })
    prices = _px([{
        "Date": "2026-08-10",
        "Open": 103,
        "High": 106,
        "Low": 100,
        "Close": 104,
        "EMA20": 102,
        "ATR14": 2,
    }])
    updated, alert = update_active_trade(trade, None, prices, 20)
    assert alert is None
    assert updated["Holding_Days"] == 3


def test_exit_new_session_increments_holding_days_once():
    trade = pd.Series({
        "Symbol": "BBCA",
        "Entry_Price": 100,
        "Initial_Stop": 95,
        "Current_Stop": 95,
        "Target_1": 110,
        "Target_2": 120,
        "Highest_Close": 103,
        "Holding_Days": 3,
        "Status": "ACTIVE",
        "Last_Update": "2026-08-10",
    })
    prices = _px([{
        "Date": "2026-08-11",
        "Open": 103,
        "High": 106,
        "Low": 100,
        "Close": 104,
        "EMA20": 102,
        "ATR14": 2,
    }])
    updated, alert = update_active_trade(trade, None, prices, 20)
    assert alert is None
    assert updated["Holding_Days"] == 4


def test_backtest_legacy_fallback_is_non_recursive():
    signal = pd.Series({
        "Signal_Date": pd.Timestamp("2026-08-01"),
        "Symbol": "BBCA",
        "Gated_Decision": "BUY READY",
        "Estimated_Slippage_Pct": 0.0,
    })
    prices = _px([
        {"Date": "2026-08-04", "Open": 100, "High": 104, "Low": 99, "Close": 103},
        {"Date": "2026-08-05", "Open": 103, "High": 105, "Low": 101, "Close": 104},
    ])
    result = evaluate_signal(signal, prices, [1], "next_open", 20)
    assert result["Status"] == "OK"
    assert result["Lifecycle_Contract_Version"] == "LEGACY_NO_EXECUTABLE_PLAN"
