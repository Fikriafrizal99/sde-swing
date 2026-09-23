from __future__ import annotations

from modules.job_runner.enhanced_runtime_bridge import _final_watchlist_buy_cost
from modules.telegram.final_watchlist_compact_ui import format_watchlist_detail


def test_compact_card_uses_ratio_cost_trend_and_human_trigger_without_touching_plan() -> None:
    row = {
        "symbol": "BMRI",
        "decision": "BUY ON TRIGGER",
        "confidence": 72,
        "setup": "PULLBACK",
        "analysis_date": "2026-08-27",
        "last_price": 4210,
        "entry_low": 4163,
        "entry_high": 4205,
        "active_stop_loss": 4101,
        "target_1": 4309,
        "target_2": 4413,
        "risk_reward": 2.0,
        "trend": "BULLISH_MODERATE",
        "phase": "WAIT TRIGGER",
        "support": 4060,
        "resistance": 4240,
        "broker_status": "NEUTRAL",
        "broker_score": 47,
        "broker_net_flow": -131_710_000_000,
        "broker_buy_ratio": 49.23,
        "broker_sell_ratio": 38.97,
        "bandar_buy_cost": 4188,
        "distance_to_buy_cost": 0.5253,
        "top_buyers": [
            {"broker": "ZP", "value": 131_640_000_000},
            {"broker": "BK", "value": 99_590_000_000},
            {"broker": "KZ", "value": 69_720_000_000},
        ],
        "top_sellers": [
            {"broker": "BB", "value": 68_190_000_000},
            {"broker": "XL", "value": 142_670_000_000},
            {"broker": "OD", "value": 61_530_000_000},
        ],
        "trigger_description": '["VOLUME_CONFIRMATION_PENDING", "ENTRY_NOT_TRIGGERED"]',
    }

    message = format_watchlist_detail(row)

    assert "📊 Bullish Moderate | WAIT TRIGGER" in message
    assert "Net -Rp131,71B | B/S 49,23%/38,97%" in message
    assert "Cost 4.188 (+0,53%)" in message
    assert "Trigger: Tunggu konfirmasi volume; entry belum terpicu. Jangan chase." in message

    # Locked trade-plan facts must remain presentation-only pass-through values.
    assert "💰 4.210 | Entry 4.163–4.205" in message
    assert "🛑 4.101 | 🎯 4.309 / 4.413 | RR 1:2,00" in message


def test_buy_cost_falls_back_to_existing_raw_fact_when_primary_is_nan() -> None:
    primary = {"avg_buyer_price": float("nan")}
    raw = {"Bandar_Buy_Cost": 4188}

    assert _final_watchlist_buy_cost(primary, raw) == 4188


def test_buy_cost_does_not_recalculate_when_no_source_fact_exists() -> None:
    assert _final_watchlist_buy_cost({}, {}) == ""

def test_compact_card_unwraps_nested_waiting_trigger_json() -> None:
    row = {
        "symbol": "GULA",
        "decision": "BUY ON TRIGGER",
        "confidence": 63,
        "setup": "EARLY ACCUMULATION",
        "analysis_date": "2026-09-23",
        "entry_low": 822,
        "entry_high": 838,
        "resistance": 855,
        "last_price": 830,
        "phase": "WAIT TRIGGER",
        "waiting_triggers": [
            '["SIDEWAYS_TRIGGER_CONFIRMATION", "ENTRY_TRIGGER_REQUIRED", "ENTRY_NOT_TRIGGERED"]'
        ],
    }

    message = format_watchlist_detail(row)

    assert "SIDEWAYS_TRIGGER_CONFIRMATION" not in message
    assert "ENTRY_TRIGGER_REQUIRED" not in message
    assert "Tunggu konfirmasi di kondisi sideways; tunggu trigger entry" in message

