from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from modules.analytics.profile_shadow import comparison_metrics, run_profiles
from modules.decision_engine.moderate_profiles import assess_liquidity, resolve_profile
from modules.decision_engine.smart_selective_v162 import smart_decision
from modules.entry_plan_validator.validator import finalize_entry_plan
from modules.runtime_config import load_runtime_config

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return load_runtime_config(ROOT / "config" / "pipeline.json")[0]["decision"]


def _row(**overrides):
    row = {
        "Symbol": "BFIN",
        "Setup_Type": "PULLBACK",
        "Technical_Quality_Score": 79,
        "Technical_Score_Final": 79,
        "Entry_Readiness_PreScore": 18,
        "Broker_Score": 78,
        "Broker_Direction": "ACCUMULATION",
        "Broker_Confirmation": "STRONG ACCUMULATION",
        "Broker_Confidence": 82,
        "Broker_Direction_Score": 72,
        "Broker_Concentration_Component": 20,
        "Broker_Pattern_Component": 18,
        "Domestic_Flow_Score": 65,
        "Foreign_Score": 62,
        "Foreign_Confidence": 70,
        "Foreign_Direction": "POSITIVE",
        "Relative_Rank_Pct": 5,
        "ATR_Extension": 1.1,
        "Turnover_MA_20": 25_000_000_000,
        "Turnover_Value": 18_000_000_000,
        "Frequency": 4_000,
        "Spread_Pct": 0.25,
        "Bid_Offer_Depth_Value": 500_000_000,
        "Market_Regime": "BULL",
    }
    row.update(overrides)
    return row


def test_bfin_like_strong_setup_is_not_avoided_only_for_low_readiness() -> None:
    result = smart_decision(_row(), 82, "LIQUID", "BULL", policy=_config(), profile_name="MODERATE_BALANCED")
    assert result["Decision_Status_Final"] == "BUY ON TRIGGER"
    assert "ENTRY_NOT_TRIGGERED" in json.loads(result["Execution_Conditions"])
    assert "ENTRY_READINESS_BELOW_MINIMUM" not in json.loads(result["Hard_Blockers"])


def test_three_profiles_have_requested_readiness_and_foreign_weights() -> None:
    cfg = _config()
    expected = {
        "MODERATE_BASELINE": (0.18, 0.08),
        "MODERATE_BALANCED": (0.15, 0.06),
        "MODERATE_FLEXIBLE": (0.12, 0.05),
    }
    for name, weights in expected.items():
        profile = resolve_profile(cfg, name)
        assert profile["weights"]["entry_readiness"] == pytest.approx(weights[0])
        assert profile["weights"]["foreign"] == pytest.approx(weights[1])


def test_broker_neutral_is_not_penalized_and_distribution_is_soft() -> None:
    neutral = smart_decision(_row(Broker_Direction="NEUTRAL", Broker_Confirmation="NEUTRAL", Broker_Confidence=45), 82, "LIQUID", "BULL", policy=_config(), profile_name="MODERATE_BALANCED")
    distribution = smart_decision(_row(Broker_Direction="DISTRIBUTION", Broker_Confirmation="DISTRIBUTION", Broker_Confidence=55, Broker_Direction_Score=-35), 82, "LIQUID", "BULL", policy=_config(), profile_name="MODERATE_BALANCED")
    assert neutral["Broker_Context"] == "NEUTRAL"
    assert "BROKER_NEUTRAL" not in json.loads(neutral["Soft_Penalties"])
    assert distribution["Broker_Context"] == "DISTRIBUTION"
    assert "BROKER_DISTRIBUTION" in json.loads(distribution["Soft_Penalties"])
    assert "STRONG_BROKER_DISTRIBUTION" not in json.loads(distribution["Hard_Blockers"])


def test_strong_distribution_is_still_hard_blocker() -> None:
    result = smart_decision(_row(Broker_Direction="DISTRIBUTION", Broker_Confirmation="STRONG DISTRIBUTION", Broker_Confidence=90, Broker_Direction_Score=-80), 82, "LIQUID", "BULL", policy=_config())
    assert result["Decision_Status_Final"] == "AVOID"
    assert "STRONG_BROKER_DISTRIBUTION" in json.loads(result["Hard_Blockers"])


def test_thin_liquidity_is_conditional_not_double_rejected() -> None:
    facts = assess_liquidity(_row(Turnover_MA_20=6_000_000_000, Turnover_Value=3_000_000_000, Frequency=900, Spread_Pct=0.9, Bid_Offer_Depth_Value=80_000_000), liquidity_score=48)
    assert facts["classification"] == "THIN_BUT_TRADEABLE"
    result = smart_decision(_row(Turnover_MA_20=6_000_000_000, Turnover_Value=3_000_000_000, Frequency=900, Spread_Pct=0.9, Bid_Offer_Depth_Value=80_000_000), 48, "THIN", "BULL", policy=_config())
    assert "LIQUIDITY_VERY_POOR" not in json.loads(result["Hard_Blockers"])
    assert result["Position_Size_Multiplier"] < 1.0


def test_bear_regime_requires_trigger_and_smaller_size_not_universal_avoid() -> None:
    result = smart_decision(_row(), 82, "LIQUID", "BEAR", policy=_config(), profile_name="MODERATE_BALANCED")
    assert result["Decision_Status_Final"] in {"BUY ON TRIGGER", "WATCH"}
    assert "BEAR_MARKET_CONDITIONAL" in json.loads(result["Soft_Penalties"])
    assert result["Position_Size_Multiplier"] <= 0.55


def test_entry_validator_does_not_convert_low_readiness_to_avoid() -> None:
    final = finalize_entry_plan(preplan_status="BUY ON TRIGGER", plan_status="REJECT", plan_reason="ENTRY_READINESS_BELOW_MINIMUM", trigger_confirmed=False)
    assert final["Decision_Status_Final"] == "BUY ON TRIGGER"


def test_shadow_profiles_use_same_input_and_produce_comparison() -> None:
    facts = pd.DataFrame([_row(), _row(Symbol="WEAK", Technical_Quality_Score=52, Technical_Score_Final=52, Entry_Readiness_PreScore=30, Broker_Direction="NEUTRAL", Broker_Confirmation="NEUTRAL")])
    shadow = run_profiles(facts, decision_config=_config())
    assert len(shadow) == len(facts) * 3
    assert shadow.groupby("Moderate_Profile")["Symbol"].nunique().eq(len(facts)).all()
    comparison = comparison_metrics(shadow)
    assert set(comparison["Moderate_Profile"]) == {"MODERATE_BASELINE", "MODERATE_BALANCED", "MODERATE_FLEXIBLE"}
    assert comparison["Expectancy_R"].isna().all()
