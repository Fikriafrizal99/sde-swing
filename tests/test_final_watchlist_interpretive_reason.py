from __future__ import annotations

from modules.telegram import _daily_report_ui
from modules.telegram.final_watchlist_ui import format_watchlist_detail


def _hrum_row() -> dict:
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
        "technical_status": "bullish",
        "confidence": 73,
        "broker_status": "STRONG_ACCUMULATION",
        "broker_score": 64,
        "broker_net_flow": 22_620_000_000,
        "buy_days": 4,
        "sell_days": 0,
        "buyer_concentration": 0.0497,
        "seller_concentration": 0.1377,
        "top_buyers": [
            {"broker": "BK", "value": 3_440_000_000, "avg_price": 850},
            {"broker": "DR", "value": 2_500_000_000, "avg_price": 850},
            {"broker": "AK", "value": 5_260_000_000, "avg_price": 850},
        ],
        "top_sellers": [
            {"broker": "XC", "value": -7_480_000_000, "avg_price": 850},
            {"broker": "BQ", "value": -1_540_000_000, "avg_price": 845},
            {"broker": "CP", "value": -1_180_000_000, "avg_price": 840},
        ],
        "bandar_buy_cost": 860,
        "distance_to_buy_cost": -1.41,
        "trend": "bullish",
        "phase": "WAIT_TRIGGER",
        "support": 720,
        "resistance": 880,
        "fib_status": "ENGINE_NOT_AVAILABLE_V1_7",
        "engine_final_reason": "generic engine reason that must not own presentation",
    }


def test_package_runtime_uses_interpretive_final_watchlist_contract():
    text = _daily_report_ui.format_watchlist_detail(_hrum_row())
    assert "<b>HRUM</b> | WATCH | 73%" in text
    assert "STRONG ACCUMULATION 64/100" in text
    assert "generic engine reason" not in text


def test_hrum_card_is_compact_and_reason_explains_why_wait():
    text = format_watchlist_detail(_hrum_row())

    assert "850 | Entry 840" in text
    assert "800 |" in text and "915 / 975" in text
    assert "Net +Rp22,62B" in text
    assert "B/S 4/0" in text
    assert "Cost 860 (-1,41%)" in text
    assert "Bullish | WAIT TRIGGER" in text
    assert "Pattern" not in text
    assert "Persistence" not in text

    assert "BK 3,44B" in text
    assert "Tunggu trigger valid di area 840–860. Jangan chase." in text
    assert "Tunggu break >880" not in text
    assert "Broker mendukung" not in text
    assert "generic engine reason" not in text
    assert len(text) <= 1024


def test_insufficient_broker_is_explained_as_missing_evidence_not_distribution():
    row = _hrum_row()
    row.update({
        "symbol": "MARK",
        "last_price": 1095,
        "entry_low": 1085,
        "entry_high": 1105,
        "active_stop_loss": 1045,
        "target_1": 1170,
        "target_2": 1230,
        "broker_status": "INSUFFICIENT",
        "broker_score": 0,
        "broker_net_flow": 2_540_000_000,
        "buy_days": 1,
        "sell_days": 0,
        "bandar_buy_cost": 1105,
        "distance_to_buy_cost": -0.73,
        "phase": "NOT_READY",
        "support": 950,
        "resistance": 1125,
    })

    text = format_watchlist_detail(row)

    assert "INSUFFICIENT 0/100" in text
    assert "Net +Rp2,54B" in text
    assert "B/S 1/0" in text
    assert "Tunggu trigger valid di area 1.085–1.105. Jangan chase." in text
    assert "Tunggu break >1.125" not in text
    assert "distribusi" not in text.lower()
    assert len(text) <= 1024


def test_tins_regression_resistance_is_context_not_implicit_trigger():
    row = _hrum_row()
    row.update({
        "symbol": "TINS",
        "decision": "BUY ON TRIGGER",
        "analysis_date": "2026-08-21",
        "last_price": 4030,
        "entry_low": 3990,
        "entry_high": 4070,
        "active_stop_loss": 3830,
        "target_1": 4310,
        "target_2": 4550,
        "risk_reward": 2.0,
        "phase": "WAIT_TRIGGER",
        "support": 3380,
        "resistance": 4190,
    })

    text = format_watchlist_detail(row)
    action = text.splitlines()[-1]

    assert "Entry 3.990–4.070" in text
    assert "S 3.380 | R 4.190" in text
    assert action == "⚠️ Tunggu trigger valid di area 3.990–4.070. Jangan chase."
    assert "4.190" not in action


def test_explicit_engine_trigger_wins_over_entry_and_resistance_inference():
    row = _hrum_row()
    row.update({
        "trigger_description": "Close di atas 900 dengan volume valid",
        "resistance": 880,
    })

    text = format_watchlist_detail(row)
    action = text.splitlines()[-1]

    assert action == "⚠️ Trigger: Close di atas 900 dengan volume valid. Jangan chase."
    assert "Tunggu break >880" not in action


def test_generic_machine_trigger_codes_fall_back_to_engine_entry_zone():
    row = _hrum_row()
    row["waiting_triggers"] = ["WAIT_FOR_ENTRY_TRIGGER", "WAIT_FOR_ENTRY_ZONE"]

    text = format_watchlist_detail(row)
    action = text.splitlines()[-1]

    assert action == "⚠️ Tunggu trigger valid di area 840–860. Jangan chase."
    assert "WAIT_FOR_" not in text
