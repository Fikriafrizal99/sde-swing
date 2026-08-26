from __future__ import annotations

from modules.telegram import _daily_report_ui
from modules.telegram.final_watchlist_ui import format_watchlist_detail


def _row() -> dict:
    return {
        "symbol": "HRUM",
        "setup": "TREND_CONTINUATION",
        "analysis_date": "2026-08-10",
        "last_price": 850,
        "entry_low": 840,
        "entry_high": 860,
        "active_stop_loss": 800,
        "target_1": 915,
        "target_2": 975,
        "risk_reward": 2.0,
        "technical_status": "BULLISH",
        "confidence": 73,
        "broker_status": "STRONG_ACCUMULATION",
        "broker_score": 64,
        "broker_net_flow": 22_620_000_000,
        "top_buyers": [{"broker": "BK", "value": 3_440_000_000, "avg_price": 850}],
        "top_sellers": [{"broker": "XC", "value": -7_480_000_000, "avg_price": 850}],
        "bandar_buy_cost": 860,
        "distance_to_buy_cost": -1.41,
        "trend": "BULLISH",
        "phase": "WAIT_TRIGGER",
        "support": 720,
        "resistance": 880,
        "main_reason": "Fakta engine ditampilkan tanpa interpretasi broker kedua.",
    }


def test_package_runtime_and_compatibility_alias_use_one_compact_formatter() -> None:
    assert _daily_report_ui.format_watchlist_detail is format_watchlist_detail
    text = format_watchlist_detail(_row())
    assert "🏦 STRONG ACCUMULATION 64/100" in text
    assert "Net +Rp22,62B" in text
    assert "BROKER PRIMARY" not in text
    assert "Persistence" not in text


def test_waiting_state_uses_resistance_trigger_in_compact_card() -> None:
    text = format_watchlist_detail(_row())
    assert "Tunggu break >880. Jangan chase." in text
    assert "Tunggu trigger valid di area" not in text


def test_explicit_engine_trigger_wins_over_entry_and_resistance() -> None:
    row = _row()
    row["trigger_description"] = "Close di atas 900 dengan volume valid"
    text = format_watchlist_detail(row)
    assert "Trigger: Close di atas 900 dengan volume valid. Jangan chase." in text
    assert "break >880" not in text


def test_generic_machine_trigger_codes_fall_back_to_resistance_trigger() -> None:
    row = _row()
    row["waiting_triggers"] = ["WAIT_FOR_ENTRY_TRIGGER", "WAIT_FOR_ENTRY_ZONE"]
    text = format_watchlist_detail(row)
    assert "WAIT_FOR_" not in text
    assert "Tunggu break >880. Jangan chase." in text
