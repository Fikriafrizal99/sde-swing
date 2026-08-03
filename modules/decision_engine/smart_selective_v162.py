#!/usr/bin/env python3
"""Single moderate Final Decision policy for SDE Swing V1.6.2 Stage 2."""
from __future__ import annotations

import json
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from modules.decision_engine.moderate_profiles import (
    assess_liquidity,
    classify_broker_context,
    classify_extension,
    classify_foreign_context,
    number,
    resolve_profile,
    resolve_setup_profile,
    truth,
)
from modules.entry_plan_validator.validator import finalize_entry_plan


def n(value: Any, default: float = 0.0) -> float:
    return number(value, default)


def setup_profile(setup: str) -> dict[str, float]:
    """Compatibility wrapper used by older tests/imports."""
    profile = resolve_setup_profile({}, setup)
    return {
        "trigger": float(profile["composite_trigger"]),
        "quality": float(profile["technical_min"]),
        "readiness": float(profile["readiness_ready"]),
        "soft_ext": float(profile["soft_extension"]),
        "hard_ext": float(profile["hard_extension"]),
    }


def _present(row: Mapping[str, Any], *keys: str) -> bool:
    return any(str(row.get(key, "")).strip().lower() not in {"", "nan", "none", "null"} for key in keys)


def _broker_decorrelated(row: Mapping[str, Any]) -> float:
    concentration = n(row.get("Broker_Concentration_Component"))
    pattern = n(row.get("Broker_Pattern_Component"))
    domestic = n(row.get("Domestic_Flow_Score"), 50.0)
    if not _present(row, "Broker_Concentration_Component", "Broker_Pattern_Component", "Domestic_Flow_Score"):
        return float(np.clip(n(row.get("Broker_Score"), 50.0), 0.0, 100.0))
    structure = float(np.clip(50.0 + (concentration + pattern) / 2.0, 0.0, 100.0))
    return 0.55 * structure + 0.45 * domestic


def _foreign_effective(row: Mapping[str, Any]) -> float:
    foreign_score = n(row.get("Foreign_Score"), 50.0)
    confidence = n(row.get("Foreign_Confidence"), 0.0)
    confidence_weight = min(max(confidence / 60.0, 0.0), 1.0)
    return float(np.clip(50.0 + (foreign_score - 50.0) * confidence_weight, 0.0, 100.0))


def smart_decision(
    row: pd.Series | dict[str, Any],
    liquidity_score: float,
    liquidity_class: str,
    market_regime: str,
    policy: dict[str, Any] | None = None,
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Calculate the only pre-plan final decision.

    This function evaluates stock attractiveness and context.  It never emits
    BUY READY: execution readiness is finalized later by Entry Plan Validator.
    """
    data = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    decision_config = dict(policy or {})
    profile = resolve_profile(decision_config, profile_name)
    setup_cfg = resolve_setup_profile(decision_config, str(data.get("Setup_Type", "DEVELOPING")))
    setup = str(setup_cfg["setup_name"])

    quality = n(data.get("Technical_Quality_Score"), n(data.get("Technical_Score_Final")))
    readiness = n(data.get("Entry_Readiness_PreScore"), min(quality, 70.0))
    rank_pct = n(data.get("Relative_Rank_Pct"), 50.0)
    rank_score = float(np.clip(100.0 - rank_pct, 0.0, 100.0))
    broker_score = _broker_decorrelated(data)
    foreign_effective = _foreign_effective(data)

    # Liquidity classification is shared with the Entry Plan Validator.  The
    # class affects confidence/size once, while VERY_POOR remains a hard blocker.
    portfolio_cfg = decision_config.get("portfolio", {}) if isinstance(decision_config.get("portfolio", {}), Mapping) else {}
    micro_cfg = decision_config.get("microstructure", {}) if isinstance(decision_config.get("microstructure", {}), Mapping) else {}
    liq = assess_liquidity(
        data,
        liquidity_score=liquidity_score,
        reference_capital=number(portfolio_cfg.get("reference_capital"), 10_000_000.0),
        max_position_pct=number(portfolio_cfg.get("max_position_pct"), 0.20),
        max_market_participation_pct=number(portfolio_cfg.get("max_market_participation_pct"), 0.02),
        minimum_required_metrics=int(number(micro_cfg.get("minimum_required_metrics"), 2)),
        missing_data_position_multiplier=number(micro_cfg.get("missing_data_position_multiplier"), 0.35),
    )
    supplied_class = str(liquidity_class or "").upper().replace(" ", "_")
    if supplied_class in {"ILLIQUID", "VERY_POOR"} and liq["classification"] == "NORMAL":
        liq["classification"] = "THIN_BUT_TRADEABLE"
    broker = classify_broker_context(data)
    foreign = classify_foreign_context(data)
    extension_value = n(data.get("ATR_Extension"), np.nan)
    extension_class = classify_extension(extension_value, setup_cfg)
    regime = str(market_regime or "UNKNOWN").upper()
    if regime == "BEARISH":
        regime = "BEAR"

    weights = profile["weights"]
    legacy_input = truth(data.get("_Legacy_Input"))
    composite = (
        weights["technical_quality"] * quality
        + weights["entry_readiness"] * readiness
        + weights["liquidity"] * float(np.clip(liquidity_score, 0.0, 100.0))
        + weights["broker_decorrelated"] * broker_score
        + weights["foreign"] * foreign_effective
        + weights["relative_rank"] * rank_score
    )
    if legacy_input:
        composite = (
            0.50 * quality
            + 0.30 * n(data.get("Broker_Score"), broker_score)
            + 0.20 * float(np.clip(liquidity_score, 0.0, 100.0))
            + min(max(n(data.get("Synergy_Bonus")), 0.0), 5.0)
            - max(n(data.get("Risk_Penalty")), 0.0)
        )

    adjustments: list[tuple[str, float]] = []
    broker_adjustment = 0.0 if legacy_input else float(profile["broker_adjustments"].get(broker["state"], 0.0))
    foreign_adjustment = 0.0 if legacy_input else float(profile["foreign_adjustments"].get(foreign["state"], 0.0))
    market_adjustment = 0.0 if legacy_input else float(profile["market_adjustments"].get(regime, 0.0))
    for label, value in ((f"BROKER_{broker['state']}", broker_adjustment), (f"FOREIGN_{foreign['state']}", foreign_adjustment), (f"MARKET_{regime}", market_adjustment)):
        if value:
            adjustments.append((label, value))
            composite += value

    volume_ratio = n(data.get("Volume_Ratio_20"), n(data.get("Volume_Ratio"), 0.0))
    explicit_volume_pass = str(data.get("Volume_Confirmation_Pass", "")).strip().lower()
    if explicit_volume_pass in {"true", "1", "yes"}:
        volume_confirmation_pass = True
    elif explicit_volume_pass in {"false", "0", "no"}:
        volume_confirmation_pass = False
    elif volume_ratio > 0:
        volume_confirmation_pass = volume_ratio >= float(setup_cfg.get("volume_ratio_min", 1.0))
    else:
        volume_confirmation_pass = None

    hard: list[str] = []
    soft: list[str] = []
    conditions: list[str] = []
    if truth(data.get("Invalid_Data")) or str(data.get("Data_Quality_Status", "VALID")).upper() in {"INVALID", "FAILED"}:
        hard.append("INVALID_DATA")
    if truth(data.get("Suspended")) or str(data.get("Tradeable_Status", "")).upper() in {"SUSPENDED", "NOT_TRADEABLE"}:
        hard.append("NOT_TRADEABLE")
    if truth(data.get("Entry_Hard_Blocker")):
        hard.append(str(data.get("Entry_Hard_Blocker_Reason") or "ENTRY_HARD_BLOCKER"))
    if broker["strong_distribution"]:
        hard.append("STRONG_BROKER_DISTRIBUTION")
    if extension_class == "HARD_EXTENDED":
        hard.append("PRICE_EXTENDED_HARD")
    elif extension_class == "SOFT_EXTENDED":
        soft.append("PRICE_EXTENDED_SOFT")
        conditions.append("WAIT_PULLBACK_OR_CONFIRMATION")
        composite -= 4.0
    if liq["classification"] == "VERY_POOR":
        hard.append("LIQUIDITY_VERY_POOR")
    elif liq["classification"] == "INSUFFICIENT_MICROSTRUCTURE_DATA":
        soft.append("INSUFFICIENT_MICROSTRUCTURE_DATA")
        conditions.extend(["MICROSTRUCTURE_CONFIRMATION_REQUIRED", "CHECK_SPREAD_SLIPPAGE", "REDUCE_POSITION_SIZE"])
    elif liq["classification"] == "THIN_BUT_TRADEABLE":
        soft.append("THIN_BUT_TRADEABLE")
        conditions.extend(["CHECK_SPREAD_SLIPPAGE", "REDUCE_POSITION_SIZE"])
        if not legacy_input:
            composite -= 2.0
    if broker["state"] == "DIVERGENCE":
        soft.append("BROKER_DIVERGENCE")
    elif broker["state"] == "DISTRIBUTION":
        soft.append("BROKER_DISTRIBUTION")
    if foreign["state"] == "NEGATIVE_MILD":
        soft.append("FOREIGN_NEGATIVE_MILD")
    elif foreign["state"] == "NEGATIVE_STRONG":
        soft.append("FOREIGN_NEGATIVE_STRONG")
    if foreign["state"] == "NEGATIVE_STRONG" and broker["state"] == "STRONG_DISTRIBUTION":
        hard.append("FOREIGN_NEGATIVE_WITH_STRONG_DISTRIBUTION")
    if volume_confirmation_pass is False:
        conditions.append("VOLUME_CONFIRMATION_PENDING")
    if regime == "SIDEWAYS":
        conditions.append("SIDEWAYS_TRIGGER_CONFIRMATION")
    elif regime == "BEAR":
        soft.append("BEAR_MARKET_CONDITIONAL")
        conditions.extend(["BEAR_TRIGGER_CONFIRMATION", "REDUCE_POSITION_SIZE", "LIMIT_ACTIVE_POSITIONS"])

    composite = float(np.clip(composite, 0.0, 100.0))
    strong_setup = quality >= float(setup_cfg["strong_quality"])
    technical_pass = quality >= float(setup_cfg["technical_min"])
    trigger_threshold = float(setup_cfg["composite_trigger"])
    if strong_setup:
        trigger_threshold -= float(profile.get("strong_setup_trigger_relief", 0.0))
    if regime == "BEAR":
        trigger_threshold = max(trigger_threshold, float(profile.get("minimum_bear_confidence", 70.0)))
    if extension_class == "SOFT_EXTENDED" or liq["classification"] in {"THIN_BUT_TRADEABLE", "INSUFFICIENT_MICROSTRUCTURE_DATA"} or regime in {"SIDEWAYS", "BEAR"}:
        conditions.append("ENTRY_TRIGGER_REQUIRED")

    if hard:
        status = "AVOID"
        reason = hard[0]
    elif technical_pass and composite >= trigger_threshold:
        # Readiness controls execution timing only.  A strong BFIN-like setup is
        # retained as BUY ON TRIGGER even when readiness is currently low.
        status = "BUY ON TRIGGER"
        reason = "AWAITING_ENTRY_PLAN_CONFIRMATION"
        conditions.append("ENTRY_NOT_TRIGGERED")
    elif composite >= float(setup_cfg["watch_score"]) and quality >= max(55.0, float(setup_cfg["technical_min"]) - 8.0):
        status = "WATCH"
        reason = "SETUP_DEVELOPING"
    else:
        status = "AVOID"
        reason = "LOW_COMPOSITE_SCORE"
        hard.append(reason)

    hard = list(dict.fromkeys(hard))
    soft = list(dict.fromkeys(soft))
    conditions = list(dict.fromkeys(conditions))
    rejected = list(dict.fromkeys(hard + soft + conditions))
    position_multiplier = float(liq["position_size_multiplier"])
    if liq["classification"] == "THIN_BUT_TRADEABLE":
        position_multiplier = min(position_multiplier, float(profile["thin_position_multiplier"]))
    elif liq["classification"] == "INSUFFICIENT_MICROSTRUCTURE_DATA":
        position_multiplier = min(position_multiplier, number(micro_cfg.get("missing_data_position_multiplier"), 0.35))
    if regime == "BEAR":
        position_multiplier = min(position_multiplier, float(profile["bear_position_multiplier"]))

    trace = [
        f"POLICY={decision_config.get('policy', 'MODERATE_SINGLE_DECISION_V1_6_2')}",
        f"PROFILE={profile['name']}",
        f"SETUP={setup}",
        f"TECH={quality:.1f}",
        f"READINESS={readiness:.1f}",
        f"BROKER_STATE={broker['state']}",
        f"BROKER_DECORR={broker_score:.1f}",
        f"FOREIGN_STATE={foreign['state']}",
        f"FOREIGN={foreign_effective:.1f}",
        f"LIQUIDITY={liq['classification']}",
        f"EXTENSION={extension_class}",
        f"VOLUME_CONFIRMATION={volume_confirmation_pass if volume_confirmation_pass is not None else 'UNKNOWN'}",
        f"MARKET={regime}",
        f"COMPOSITE={composite:.1f}",
        f"STATUS={status}",
    ]
    if legacy_input and status == "BUY ON TRIGGER" and composite >= 72.0:
        legacy = "BUY"
    else:
        legacy = "BUY CANDIDATE" if status == "BUY ON TRIGGER" else status
    return {
        "Decision_Status": status,
        "Decision_Status_PrePlan": status,
        "Decision_Status_Final": status,
        "Decision_V3": legacy,
        "Decision_Reason_Code": reason,
        "Final_Score_V3": round(composite, 2),
        "Broker_Score_Decorrelated": round(broker_score, 2),
        "Broker_Context": broker["state"],
        "Foreign_Score_Effective": round(foreign_effective, 2),
        "Foreign_Context": foreign["state"],
        "Liquidity_Execution_Class": liq["classification"],
        "Liquidity_Estimated_Slippage_Pct": round(float(liq["estimated_slippage_pct"]), 4),
        "Liquidity_Available_Metric_Count": int(liq.get("available_metric_count", 0)),
        "Liquidity_Missing_Metrics": json.dumps(liq.get("missing_metrics", []), ensure_ascii=False),
        "Market_Participation": liq.get("market_participation"),
        "Extension_Class": extension_class,
        "Volume_Confirmation_Pass": volume_confirmation_pass,
        "Position_Size_Multiplier": round(max(0.0, min(position_multiplier, 1.0)), 4),
        "Execution_Conditions": json.dumps(conditions, ensure_ascii=False),
        "Rejected_By": json.dumps(rejected, ensure_ascii=False),
        "Hard_Blockers": json.dumps(hard, ensure_ascii=False),
        "Soft_Penalties": json.dumps(soft, ensure_ascii=False),
        "Decision_Trace": json.dumps(trace, ensure_ascii=False),
        "Threshold_Profile": setup,
        "Moderate_Profile": profile["name"],
        "Decision_Weights": json.dumps(weights, ensure_ascii=False, sort_keys=True),
        "Decision_Adjustments": json.dumps(adjustments, ensure_ascii=False),
        "Decision_Owner": "DECISION_ENGINE",
    }


def finalize_after_entry_plan(**kwargs: Any) -> dict[str, Any]:
    """Backward-compatible import alias for Stage 1 callers/tests."""
    return finalize_entry_plan(**kwargs)
