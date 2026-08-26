from __future__ import annotations

import pandas as pd

from modules.telegram.daily_report_ui import format_watchlist_detail
from modules.telegram.formatters import exchange_warnings, format_momentum, human_status, risk_reward
from modules.telegram.professional_ui import format_signal_detail


def test_shared_formatter_contract_is_human_and_bounded() -> None:
    assert human_status("BUY ON TRIGGER") == "BUY CANDIDATE"
    assert human_status("WATCH HIGH") == "WATCH"
    assert format_momentum(58, 0.4) == "SEHAT — RSI 58,0"
    assert format_momentum(78, 1.0).startswith("OVERBOUGHT")
    assert format_momentum(35, -0.2).startswith("LEMAH")
    assert risk_reward(100, 102, 110, 95) == ("1:1,50", True)
    assert risk_reward(100, 102, 98, 105)[0] == "R:R belum valid"
    assert exchange_warnings("NORMAL", ["UMA"])[0].startswith("UMA —")
    assert exchange_warnings("SUSPENDED", [], "SUSPENDED")[0].startswith("SUSPENDED —")


def test_detail_reports_escape_dynamic_values_and_use_compact_contract() -> None:
    payload = {
        "rank": 1,
        "symbol": "BBCA<&",
        "decision": "BUY READY",
        "confidence": 84.6789,
        "trade_date": "2026-08-06",
        "setup": "TREND_CONTINUATION",
        "trend": "BULLISH",
        "technical_quality": 82.345,
        "entry_readiness": 76.2,
        "rsi": 58,
        "macd_hist": 0.4,
        "entry_low": 100,
        "entry_high": 102,
        "target_1": 110,
        "target_2": 115,
        "stop_loss": 95,
        "waiting_triggers": ["WAIT_FOR_ENTRY_TRIGGER", "WAIT_FOR_ENTRY_ZONE", "third item"],
        "risk_items": ["PRICE_EXTENDED", "UMA"],
        "risk_flags": ["UMA"],
        "main_reason": ["should not render as a Python list"],
        "broker_status": "ACCUMULATION",
        "broker_net_flow": 1_200_000_000,
        "top_buyers": [{"broker": "AB", "value": 100_000_000, "avg_price": 101}],
        "top_sellers": [{"broker": "CD", "value": -80_000_000, "avg_price": 102}],
    }
    text = format_watchlist_detail(payload)
    assert "BBCA&lt;&amp;" in text
    assert "BUY READY" in text
    assert "WAIT_FOR_ENTRY_TRIGGER" not in text
    assert "['should not render" not in text
    assert "should not render as a Python list" not in text
    assert "🏦 ACCUMULATION" in text
    assert "Net +Rp1,2B" in text
    assert "AB 100M" in text
    assert "CD 80M" in text
    assert "Reason:" not in text


def test_professional_signal_downgrades_ready_when_rr_is_not_valid() -> None:
    row = pd.Series({"Symbol": "BBCA", "Decision_V3": "BUY", "Final_Score_V3": 90, "RSI_14": 58, "MACD_Hist": 1})
    plan = pd.Series({"Plan_Status": "ACCEPT", "Entry_Zone_Low": 100, "Entry_Zone_High": 102, "Initial_Stop": 105, "Target_1": 110})
    text = format_signal_detail("2026-08-06", row, plan)
    assert "WAITING" in text
    assert "R:R TP1: R:R belum valid" in text
