#!/usr/bin/env python3
"""Moderate swing profile definitions and shared risk/context classifiers.

This module contains no final-decision side effects.  It only resolves the
selected profile and converts raw market context into transparent facts that
can be consumed by the single Final Decision Engine and Entry Plan Validator.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping

import numpy as np


DEFAULT_SETUP_PROFILES: dict[str, dict[str, float]] = {
    "BREAKOUT": {
        "technical_min": 67.0,
        "strong_quality": 72.0,
        "composite_trigger": 65.0,
        "watch_score": 56.0,
        "readiness_ready": 66.0,
        "readiness_trigger_reference": 45.0,
        "volume_ratio_min": 1.20,
        "soft_extension": 2.60,
        "hard_extension": 3.30,
        "min_rr": 1.00,
        "preferred_rr": 2.00,
        "support_lookback": 20.0,
        "resistance_lookback": 120.0,
    },
    "PULLBACK": {
        "technical_min": 67.0,
        "strong_quality": 72.0,
        "composite_trigger": 64.0,
        "watch_score": 55.0,
        "readiness_ready": 64.0,
        "readiness_trigger_reference": 42.0,
        "volume_ratio_min": 0.80,
        "soft_extension": 2.20,
        "hard_extension": 2.80,
        "min_rr": 1.00,
        "preferred_rr": 2.00,
        "support_lookback": 30.0,
        "resistance_lookback": 120.0,
    },
    "TREND_CONTINUATION": {
        "technical_min": 68.0,
        "strong_quality": 73.0,
        "composite_trigger": 65.0,
        "watch_score": 56.0,
        "readiness_ready": 65.0,
        "readiness_trigger_reference": 44.0,
        "volume_ratio_min": 0.90,
        "soft_extension": 2.50,
        "hard_extension": 3.00,
        "min_rr": 1.00,
        "preferred_rr": 2.00,
        "support_lookback": 25.0,
        "resistance_lookback": 120.0,
    },
    "EARLY_ACCUMULATION": {
        "technical_min": 61.0,
        "strong_quality": 68.0,
        "composite_trigger": 63.0,
        "watch_score": 54.0,
        "readiness_ready": 58.0,
        "readiness_trigger_reference": 35.0,
        "volume_ratio_min": 0.65,
        "soft_extension": 2.00,
        "hard_extension": 2.60,
        "min_rr": 1.00,
        "preferred_rr": 1.80,
        "support_lookback": 30.0,
        "resistance_lookback": 120.0,
    },
    "DEVELOPING": {
        "technical_min": 67.0,
        "strong_quality": 74.0,
        "composite_trigger": 66.0,
        "watch_score": 57.0,
        "readiness_ready": 66.0,
        "readiness_trigger_reference": 48.0,
        "volume_ratio_min": 1.00,
        "soft_extension": 2.40,
        "hard_extension": 3.00,
        "min_rr": 1.00,
        "preferred_rr": 2.00,
        "support_lookback": 20.0,
        "resistance_lookback": 120.0,
    },
}

DEFAULT_MODERATE_PROFILES: dict[str, dict[str, Any]] = {
    "MODERATE_BASELINE": {
        "description": "Audit V1.6.2 baseline; production control profile.",
        "weights": {
            "technical_quality": 0.42,
            "entry_readiness": 0.18,
            "liquidity": 0.10,
            "broker_decorrelated": 0.17,
            "foreign": 0.08,
            "relative_rank": 0.05,
        },
        "strong_setup_trigger_relief": 0.0,
        "broker_adjustments": {
            "STRONG_ACCUMULATION": 3.0,
            "ACCUMULATION": 1.5,
            "NEUTRAL": 0.0,
            "DIVERGENCE": -3.0,
            "DISTRIBUTION": -6.0,
        },
        "foreign_adjustments": {
            "POSITIVE": 2.0,
            "NEUTRAL": 0.0,
            "NEGATIVE_MILD": -2.0,
            "NEGATIVE_STRONG": -4.0,
        },
        "market_adjustments": {"BULL": 0.0, "SIDEWAYS": 0.0, "BEAR": -3.0},
        "thin_position_multiplier": 0.60,
        "bear_position_multiplier": 0.60,
        "sideways_trigger_required": True,
        "bear_trigger_required": True,
        "minimum_bear_confidence": 68.0,
        "bear_max_active_positions": 2,
    },
    "MODERATE_BALANCED": {
        "description": "Lower readiness/foreign influence; neutral broker is not penalized.",
        "weights": {
            "technical_quality": 0.45,
            "entry_readiness": 0.15,
            "liquidity": 0.10,
            "broker_decorrelated": 0.19,
            "foreign": 0.06,
            "relative_rank": 0.05,
        },
        "strong_setup_trigger_relief": 2.0,
        "broker_adjustments": {
            "STRONG_ACCUMULATION": 3.0,
            "ACCUMULATION": 1.5,
            "NEUTRAL": 0.0,
            "DIVERGENCE": -2.5,
            "DISTRIBUTION": -5.0,
        },
        "foreign_adjustments": {
            "POSITIVE": 1.5,
            "NEUTRAL": 0.0,
            "NEGATIVE_MILD": -1.5,
            "NEGATIVE_STRONG": -3.5,
        },
        "market_adjustments": {"BULL": 0.0, "SIDEWAYS": 0.0, "BEAR": -2.0},
        "thin_position_multiplier": 0.55,
        "bear_position_multiplier": 0.55,
        "sideways_trigger_required": True,
        "bear_trigger_required": True,
        "minimum_bear_confidence": 70.0,
        "bear_max_active_positions": 2,
    },
    "MODERATE_FLEXIBLE": {
        "description": "Shadow-only opportunity-capture profile for strong setups.",
        "weights": {
            "technical_quality": 0.47,
            "entry_readiness": 0.12,
            "liquidity": 0.10,
            "broker_decorrelated": 0.21,
            "foreign": 0.05,
            "relative_rank": 0.05,
        },
        "strong_setup_trigger_relief": 4.0,
        "broker_adjustments": {
            "STRONG_ACCUMULATION": 3.0,
            "ACCUMULATION": 1.5,
            "NEUTRAL": 0.0,
            "DIVERGENCE": -2.0,
            "DISTRIBUTION": -4.5,
        },
        "foreign_adjustments": {
            "POSITIVE": 1.0,
            "NEUTRAL": 0.0,
            "NEGATIVE_MILD": -1.0,
            "NEGATIVE_STRONG": -3.0,
        },
        "market_adjustments": {"BULL": 0.0, "SIDEWAYS": 0.0, "BEAR": -1.5},
        "thin_position_multiplier": 0.50,
        "bear_position_multiplier": 0.50,
        "sideways_trigger_required": True,
        "bear_trigger_required": True,
        "minimum_bear_confidence": 72.0,
        "bear_max_active_positions": 1,
    },
}


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else default
    except Exception:
        return default


def text(value: Any, default: str = "") -> str:
    raw = str(value if value is not None else default).strip()
    return default if raw.lower() in {"", "nan", "none", "null"} else raw


def truth(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_profile(decision_config: Mapping[str, Any] | None, profile_name: str | None = None) -> dict[str, Any]:
    decision_config = dict(decision_config or {})
    selected = str(profile_name or decision_config.get("production_profile") or "MODERATE_BASELINE").upper()
    configured = decision_config.get("profiles", {})
    if selected not in DEFAULT_MODERATE_PROFILES and selected not in configured:
        raise ValueError(f"UNKNOWN_MODERATE_PROFILE: {selected}")
    profile = copy.deepcopy(DEFAULT_MODERATE_PROFILES.get(selected, DEFAULT_MODERATE_PROFILES["MODERATE_BASELINE"]))
    if isinstance(configured, Mapping) and isinstance(configured.get(selected), Mapping):
        profile = deep_merge(profile, configured[selected])
    profile["name"] = selected
    weights = profile.get("weights", {})
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"DECISION_WEIGHT_SUM_INVALID[{selected}]: {total}")
    return profile


def resolve_setup_profile(decision_config: Mapping[str, Any] | None, setup: str) -> dict[str, float]:
    decision_config = dict(decision_config or {})
    setup_name = str(setup or "DEVELOPING").upper()
    if setup_name == "CONTINUATION":
        setup_name = "TREND_CONTINUATION"
    base = copy.deepcopy(DEFAULT_SETUP_PROFILES.get(setup_name, DEFAULT_SETUP_PROFILES["DEVELOPING"]))
    configured = decision_config.get("setup_profiles", {})
    if isinstance(configured, Mapping) and isinstance(configured.get(setup_name), Mapping):
        base.update({k: float(v) for k, v in configured[setup_name].items()})
    base["setup_name"] = setup_name  # type: ignore[assignment]
    return base


def classify_extension(extension: float, setup_profile: Mapping[str, Any]) -> str:
    if not math.isfinite(extension):
        return "UNKNOWN"
    if extension > float(setup_profile.get("hard_extension", 3.0)):
        return "HARD_EXTENDED"
    if extension > float(setup_profile.get("soft_extension", 2.4)):
        return "SOFT_EXTENDED"
    return "NORMAL"


def classify_broker_context(row: Mapping[str, Any]) -> dict[str, Any]:
    direction = text(row.get("Broker_Direction"), "NEUTRAL").upper()
    confirmation = text(row.get("Broker_Confirmation"), "NEUTRAL").upper()
    confidence = number(row.get("Broker_Confidence"), 0.0)
    direction_score = number(row.get("Broker_Direction_Score"), 0.0)
    divergence = truth(row.get("Broker_Divergence"))

    strong_distribution = (
        confirmation == "STRONG DISTRIBUTION"
        or (direction == "DISTRIBUTION" and confidence >= 70.0 and direction_score <= -60.0)
    )
    if strong_distribution:
        state = "STRONG_DISTRIBUTION"
    elif direction == "DISTRIBUTION" or confirmation == "DISTRIBUTION":
        state = "DISTRIBUTION"
    elif divergence:
        state = "DIVERGENCE"
    elif confirmation == "STRONG ACCUMULATION" or (
        direction == "ACCUMULATION" and confidence >= 65.0 and direction_score >= 60.0
    ):
        state = "STRONG_ACCUMULATION"
    elif direction == "ACCUMULATION" or confirmation == "ACCUMULATION":
        state = "ACCUMULATION"
    else:
        state = "NEUTRAL"
    return {
        "state": state,
        "direction": direction,
        "confirmation": confirmation,
        "confidence": confidence,
        "direction_score": direction_score,
        "divergence": divergence,
        "strong_distribution": strong_distribution,
    }


def classify_foreign_context(row: Mapping[str, Any]) -> dict[str, Any]:
    score = number(row.get("Foreign_Score"), 50.0)
    confidence = number(row.get("Foreign_Confidence"), 0.0)
    net_pct = number(row.get("Foreign_Net_Pct"), 0.0)
    consistency = number(row.get("Foreign_Consistency_Days"), number(row.get("Foreign_Consistency"), 0.0))
    direction = text(row.get("Foreign_Direction"), "NO DATA").upper()

    if confidence < 20.0 or direction in {"NO DATA", "UNKNOWN"}:
        state = "NEUTRAL"
    elif score >= 55.0 or net_pct >= 1.0 or direction == "POSITIVE":
        state = "POSITIVE"
    elif (score <= 25.0 or net_pct <= -8.0) and (confidence >= 60.0 or consistency >= 3.0):
        state = "NEGATIVE_STRONG"
    elif score < 45.0 or net_pct <= -1.0 or direction == "NEGATIVE":
        state = "NEGATIVE_MILD"
    else:
        state = "NEUTRAL"
    return {
        "state": state,
        "score": score,
        "confidence": confidence,
        "net_pct": net_pct,
        "consistency": consistency,
        "direction": direction,
    }


def assess_liquidity(
    row: Mapping[str, Any],
    *,
    liquidity_score: float | None = None,
    reference_capital: float = 100_000_000.0,
    max_position_pct: float = 0.20,
    max_market_participation_pct: float = 0.02,
    minimum_required_metrics: int = 2,
    missing_data_position_multiplier: float = 0.35,
) -> dict[str, Any]:
    """Classify execution liquidity without relying on a single absolute threshold.

    Missing microstructure fields do not automatically fail a candidate.  The
    classifier records missing evidence and uses turnover as a conservative
    fallback, while VERY_POOR requires either extremely low turnover or several
    independent execution-risk signals.
    """
    average_value = number(
        row.get("Turnover_MA_20"),
        number(row.get("Average_Value_20"), number(row.get("Avg_Value_20"), 0.0)),
    )
    daily_value = number(row.get("Turnover_Value"), number(row.get("Value"), 0.0))
    frequency = number(row.get("Frequency"), number(row.get("Freq"), 0.0))
    bid = number(row.get("Bid"), 0.0)
    ask = number(row.get("Ask"), 0.0)
    spread_pct = number(row.get("Spread_Pct"), np.nan)
    if not math.isfinite(spread_pct) and bid > 0 and ask >= bid:
        spread_pct = (ask - bid) / ((ask + bid) / 2.0) * 100.0
    depth_value = number(
        row.get("Bid_Offer_Depth_Value"),
        number(row.get("Orderbook_Depth_Value"), number(row.get("Depth_Value"), 0.0)),
    )
    score = float(liquidity_score if liquidity_score is not None else number(row.get("Liquidity_Score"), 0.0))
    planned_position = max(0.0, reference_capital * max_position_pct)
    participation = planned_position / average_value if average_value > 0 else math.inf
    participation_limit = max(float(max_market_participation_pct), 0.0001)
    estimated_slippage = 0.0
    if math.isfinite(spread_pct):
        estimated_slippage += max(spread_pct, 0.0) * 0.50
    if math.isfinite(participation):
        estimated_slippage += min(participation * 12.0, 2.5)
    else:
        estimated_slippage += 2.5
    estimated_slippage = float(np.clip(estimated_slippage, 0.0, 5.0))

    poor_flags: list[str] = []
    thin_flags: list[str] = []
    missing: list[str] = []
    if average_value <= 0:
        missing.append("AVERAGE_VALUE")
    elif average_value < 1_000_000_000:
        poor_flags.append("AVERAGE_VALUE_BELOW_1B")
    elif average_value < 10_000_000_000:
        thin_flags.append("AVERAGE_VALUE_BELOW_10B")
    if daily_value > 0 and daily_value < 500_000_000:
        poor_flags.append("DAILY_VALUE_BELOW_500M")
    elif daily_value > 0 and daily_value < 5_000_000_000:
        thin_flags.append("DAILY_VALUE_BELOW_5B")
    elif daily_value <= 0:
        missing.append("DAILY_VALUE")
    if frequency > 0 and frequency < 300:
        poor_flags.append("FREQUENCY_VERY_LOW")
    elif frequency > 0 and frequency < 1_500:
        thin_flags.append("FREQUENCY_LOW")
    elif frequency <= 0:
        missing.append("FREQUENCY")
    if math.isfinite(spread_pct) and spread_pct > 2.0:
        poor_flags.append("SPREAD_ABOVE_2PCT")
    elif math.isfinite(spread_pct) and spread_pct > 0.80:
        thin_flags.append("SPREAD_ABOVE_0_8PCT")
    elif not math.isfinite(spread_pct):
        missing.append("SPREAD")
    if depth_value > 0 and planned_position > 0 and depth_value < planned_position:
        poor_flags.append("DEPTH_BELOW_POSITION")
    elif depth_value > 0 and planned_position > 0 and depth_value < planned_position * 3.0:
        thin_flags.append("DEPTH_THIN_FOR_POSITION")
    elif depth_value <= 0:
        missing.append("DEPTH")
    if math.isfinite(participation) and participation > participation_limit * 2.0:
        poor_flags.append("MARKET_PARTICIPATION_TOO_HIGH")
    elif math.isfinite(participation) and participation > participation_limit:
        thin_flags.append("MARKET_PARTICIPATION_ELEVATED")
    if estimated_slippage > 1.25:
        poor_flags.append("ESTIMATED_SLIPPAGE_HIGH")
    elif estimated_slippage > 0.50:
        thin_flags.append("ESTIMATED_SLIPPAGE_ELEVATED")
    if score and score < 20:
        poor_flags.append("LIQUIDITY_SCORE_VERY_LOW")
    elif score and score < 55:
        thin_flags.append("LIQUIDITY_SCORE_LOW")

    available_metrics = 5 - len(set(missing).intersection({"AVERAGE_VALUE", "DAILY_VALUE", "FREQUENCY", "SPREAD", "DEPTH"}))
    if average_value > 0 and average_value < 500_000_000:
        classification = "VERY_POOR"
    elif len(set(poor_flags)) >= 2:
        classification = "VERY_POOR"
    elif available_metrics < int(minimum_required_metrics):
        classification = "INSUFFICIENT_MICROSTRUCTURE_DATA"
    elif poor_flags or thin_flags:
        classification = "THIN_BUT_TRADEABLE"
    else:
        classification = "NORMAL"

    if classification == "NORMAL":
        multiplier = 1.0
    elif classification == "THIN_BUT_TRADEABLE":
        multiplier = 0.55
    elif classification == "INSUFFICIENT_MICROSTRUCTURE_DATA":
        multiplier = max(0.0, min(float(missing_data_position_multiplier), 1.0))
    else:
        multiplier = 0.0
    return {
        "classification": classification,
        "average_value": average_value,
        "daily_value": daily_value,
        "frequency": frequency,
        "spread_pct": spread_pct,
        "depth_value": depth_value,
        "estimated_slippage_pct": estimated_slippage,
        "position_size_multiplier": multiplier,
        "available_metric_count": available_metrics,
        "minimum_required_metrics": int(minimum_required_metrics),
        "market_participation": participation if math.isfinite(participation) else None,
        "market_participation_limit": participation_limit,
        "poor_flags": list(dict.fromkeys(poor_flags)),
        "thin_flags": list(dict.fromkeys(thin_flags)),
        "missing_metrics": list(dict.fromkeys(missing)),
    }
