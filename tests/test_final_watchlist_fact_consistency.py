from modules.job_runner.enhanced_runtime_bridge import (
    _final_watchlist_broker_score,
    _final_watchlist_distance,
    _final_watchlist_plan_rr,
    _final_watchlist_reason,
)


def test_final_watchlist_rr_uses_executable_target_not_minor_resistance():
    plan = {
        "RR_To_Resistance": 0.19,
        "RR_To_Minor_Resistance": 0.19,
        "Target_1_RR": 1.0,
        "Target_2_RR": 2.0,
    }
    assert _final_watchlist_plan_rr(plan) == 2.0


def test_final_watchlist_preserves_zero_multiday_confidence():
    multiday = {"broker_score": 0.0, "broker_status": "INSUFFICIENT_DATA"}
    raw = {"Broker_Score": 99.0, "Broker_Confidence_Final": 99.0}
    assert _final_watchlist_broker_score(multiday, raw) == 0.0


def test_final_watchlist_distance_uses_visible_price_and_buy_cost_when_missing():
    multiday = {
        "bandar_buy_cost": 1790.0,
        "distance_to_buy_cost": "ENGINE_DATA_NOT_AVAILABLE",
    }
    raw = {"Close": 1820.0}
    assert _final_watchlist_distance(multiday, raw) == 1.676


def test_final_watchlist_distance_preserves_engine_value_when_present():
    multiday = {
        "bandar_buy_cost": 1790.0,
        "distance_to_buy_cost": -0.55,
    }
    raw = {"Close": 1820.0}
    assert _final_watchlist_distance(multiday, raw) == -0.55


def test_final_watchlist_reason_does_not_reuse_single_session_broker_fusion_reason():
    raw = {
        "Decision_Reasons": "trend kuat; net flow positif; buyer concentration dominan",
    }
    assert _final_watchlist_reason(raw) == ""


def test_final_watchlist_keeps_explicit_engine_reason_when_available():
    raw = {
        "Main_Reason": "Setup teknikal valid dan konteks broker multi-day belum cukup data.",
        "Decision_Reasons": "net flow positif",
    }
    assert _final_watchlist_reason(raw) == raw["Main_Reason"]
