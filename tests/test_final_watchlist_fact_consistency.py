import inspect

from modules.job_runner.enhanced_runtime_bridge import (
    _final_watchlist_distance,
    _final_watchlist_plan_rr,
)
from modules.job_runner import enhanced_runtime_bridge


def test_final_watchlist_rr_uses_executable_target_not_minor_resistance():
    plan = {
        "RR_To_Resistance": 0.19,
        "RR_To_Minor_Resistance": 0.19,
        "Target_1_RR": 1.0,
        "Target_2_RR": 2.0,
    }
    assert _final_watchlist_plan_rr(plan) == 2.0


def test_final_watchlist_broker_score_stays_broker_fusion_owned():
    source = inspect.getsource(enhanced_runtime_bridge.final_watchlist_payloads)
    assert '"broker_score": _value(raw, "Broker_Score"' in source
    assert 'primary.get("broker_score"' not in source


def test_final_watchlist_distance_uses_visible_price_and_buy_cost_when_missing():
    primary = {
        "avg_buyer_price": 1790.0,
    }
    raw = {"Close": 1820.0}
    assert _final_watchlist_distance(primary, raw) == 1.676


def test_final_watchlist_distance_preserves_engine_value_when_present():
    primary = {
        "avg_buyer_price": 1790.0,
    }
    raw = {"Close": 1820.0, "Distance_To_Buy_Cost_Pct": -0.55}
    assert _final_watchlist_distance(primary, raw) == -0.55


def test_final_watchlist_distance_fails_closed_without_price_or_primary_cost():
    assert _final_watchlist_distance({}, {}) == ""
