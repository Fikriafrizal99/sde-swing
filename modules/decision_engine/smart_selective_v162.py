#!/usr/bin/env python3
"""Setup-aware smart-selective decision policy for SDE Swing V1.6.2."""
from __future__ import annotations

import json
import math
from typing import Any
import numpy as np
import pandas as pd


def n(value: Any, default: float = 0.0) -> float:
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else default
    except Exception:
        return default


def truth(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def setup_profile(setup: str) -> dict[str, float]:
    profiles = {
        "BREAKOUT": {"trigger": 65, "quality": 67, "readiness": 66, "soft_ext": 2.6, "hard_ext": 3.3},
        "PULLBACK": {"trigger": 64, "quality": 67, "readiness": 64, "soft_ext": 2.2, "hard_ext": 2.8},
        "TREND_CONTINUATION": {"trigger": 65, "quality": 68, "readiness": 65, "soft_ext": 2.5, "hard_ext": 3.0},
        "CONTINUATION": {"trigger": 65, "quality": 68, "readiness": 65, "soft_ext": 2.5, "hard_ext": 3.0},
        "EARLY_ACCUMULATION": {"trigger": 63, "quality": 61, "readiness": 50, "soft_ext": 2.0, "hard_ext": 2.6},
        "DEVELOPING": {"trigger": 66, "quality": 67, "readiness": 58, "soft_ext": 2.4, "hard_ext": 3.0},
    }
    return profiles.get(setup, profiles["DEVELOPING"])


def smart_decision(
    row: pd.Series | dict[str, Any],
    liquidity_score: float,
    liquidity_class: str,
    market_regime: str,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    setup = str(row.get("Setup_Type", "DEVELOPING") or "DEVELOPING").upper()
    profile = setup_profile(setup)
    quality = n(row.get("Technical_Quality_Score"), n(row.get("Technical_Score_Final")))
    readiness = n(row.get("Entry_Readiness_PreScore"), min(quality, 70.0))
    broker_confidence = n(row.get("Broker_Confidence"))
    broker_direction = str(row.get("Broker_Direction", "NEUTRAL") or "NEUTRAL").upper()
    broker_direction_score = n(row.get("Broker_Direction_Score"))
    concentration = n(row.get("Broker_Concentration_Component"))
    pattern = n(row.get("Broker_Pattern_Component"))
    domestic = n(row.get("Domestic_Flow_Score"), 50.0)
    foreign_score = n(row.get("Foreign_Score"), 50.0)
    foreign_confidence = n(row.get("Foreign_Confidence"))
    rank_pct = n(row.get("Relative_Rank_Pct"), 50.0)
    rank_score = float(np.clip(100.0 - rank_pct, 0.0, 100.0))
    configured_weights = (policy or {}).get("weights", {})
    weights = {
        "technical_quality": float(configured_weights.get("technical_quality", 0.42)),
        "entry_readiness": float(configured_weights.get("entry_readiness", 0.18)),
        "liquidity": float(configured_weights.get("liquidity", 0.10)),
        "broker_decorrelated": float(configured_weights.get("broker_decorrelated", 0.17)),
        "foreign": float(configured_weights.get("foreign", 0.08)),
        "relative_rank": float(configured_weights.get("relative_rank", 0.05)),
    }
    weight_sum = sum(weights.values())
    if abs(weight_sum - 1.0) > 1e-9:
        raise ValueError(f"DECISION_WEIGHT_SUM_INVALID: {weight_sum}")

    # Broker Summary aggregate flow includes foreign brokers. Use structure +
    # domestic raw flow here, then score foreign independently to avoid double counting.
    # Historical/regression inputs may not contain the new components; in that case,
    # preserve backward compatibility by using the already-computed Broker_Score once.
    has_structure = any(str(row.get(key, "")).strip().lower() not in {"", "nan", "none"} for key in (
        "Broker_Concentration_Component", "Broker_Pattern_Component"
    ))
    has_domestic = str(row.get("Domestic_Flow_Score", "")).strip().lower() not in {"", "nan", "none"}
    broker_structure = float(np.clip(50.0 + (concentration + pattern) / 2.0, 0.0, 100.0))
    if not has_structure and not has_domestic:
        broker_decorrelated = n(row.get("Broker_Score"), 50.0)
    else:
        broker_decorrelated = 0.55 * broker_structure + 0.45 * domestic
    foreign_weight = min(foreign_confidence / 60.0, 1.0)
    foreign_effective = 50.0 + (foreign_score - 50.0) * foreign_weight

    final = (
        weights["technical_quality"] * quality
        + weights["entry_readiness"] * readiness
        + weights["liquidity"] * liquidity_score
        + weights["broker_decorrelated"] * broker_decorrelated
        + weights["foreign"] * foreign_effective
        + weights["relative_rank"] * rank_score
    )
    hard: list[str] = []
    soft: list[str] = []
    extension = n(row.get("ATR_Extension"))
    turnover = n(row.get("Turnover_MA_20"))

    if truth(row.get("Entry_Hard_Blocker")):
        hard.append(str(row.get("Entry_Hard_Blocker_Reason") or "ENTRY_HARD_BLOCKER"))
    if turnover < 2_000_000_000:
        hard.append("LIQUIDITY_VERY_POOR")
    if broker_direction == "DISTRIBUTION" and broker_confidence >= 70 and broker_direction_score <= -60:
        hard.append("STRONG_BROKER_DISTRIBUTION")
    if extension > profile["hard_ext"]:
        hard.append("PRICE_EXTENDED_HARD")
    elif extension > profile["soft_ext"]:
        soft.append("PRICE_EXTENDED_SOFT")
        final -= 5.0
    if broker_direction == "DISTRIBUTION" and "STRONG_BROKER_DISTRIBUTION" not in hard:
        soft.append("BROKER_DISTRIBUTION")
        final -= 6.0
    if foreign_score < 35 and foreign_confidence >= 55:
        soft.append("FOREIGN_NEGATIVE")
        final -= 4.0
    if truth(row.get("Broker_Divergence")):
        soft.append("BROKER_DIVERGENCE")
        final -= 3.0
    if market_regime.upper() == "BEAR":
        soft.append("BEAR_MARKET_PENALTY")
        final -= 4.0 if setup == "BREAKOUT" else 2.0

    final = float(np.clip(final, 0.0, 100.0))
    if hard:
        status = "AVOID"
        reason = hard[0]
    elif final >= profile["trigger"] and quality >= profile["quality"] and readiness >= max(45.0, profile["readiness"] - 12.0):
        status = "BUY ON TRIGGER"
        reason = "AWAITING_ENTRY_PLAN_CONFIRMATION"
        soft.append("ENTRY_NOT_TRIGGERED")
    elif final >= 55 and quality >= 58:
        status = "WATCH"
        reason = "SETUP_DEVELOPING"
    else:
        status = "AVOID"
        reason = "LOW_COMPOSITE_SCORE"
        hard.append(reason)

    rejected_by = list(dict.fromkeys(hard + soft))
    trace = [
        f"POLICY={str((policy or {}).get('policy', 'SMART_SELECTIVE_V1_6_2'))}",
        f"SETUP={setup}", f"TECH={quality:.1f}", f"READINESS={readiness:.1f}",
        f"BROKER_DECORR={broker_decorrelated:.1f}", f"FOREIGN={foreign_effective:.1f}",
        f"LIQ={liquidity_score:.1f}", f"RANK_PCT={rank_pct:.1f}", f"FINAL={final:.1f}",
    ]
    legacy = "BUY CANDIDATE" if status == "BUY ON TRIGGER" else status
    return {
        "Decision_Status": status,
        "Decision_V3": legacy,
        "Decision_Reason_Code": reason,
        "Final_Score_V3": round(final, 2),
        "Broker_Score_Decorrelated": round(broker_decorrelated, 2),
        "Foreign_Score_Effective": round(foreign_effective, 2),
        "Rejected_By": json.dumps(rejected_by, ensure_ascii=False),
        "Hard_Blockers": json.dumps(hard, ensure_ascii=False),
        "Soft_Penalties": json.dumps(soft, ensure_ascii=False),
        "Decision_Trace": json.dumps(trace, ensure_ascii=False),
        "Threshold_Profile": setup,
        "Decision_Weights": json.dumps(weights, ensure_ascii=False, sort_keys=True),
    }


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return [str(item) for item in decoded if str(item).strip()]
        except Exception:
            return []
    return []


def finalize_after_entry_plan(
    *,
    preplan_status: str,
    plan_status: str,
    plan_reason: str,
    rejected_by_preplan: Any = None,
    decision_trace_preplan: Any = None,
    major_rr: float | None = None,
    risk_pct: float | None = None,
) -> dict[str, Any]:
    """Finalize execution readiness without recalculating the fusion score.

    Decision Engine remains the policy owner. Exit Engine only supplies plan facts
    (ACCEPT/CONDITIONAL/REJECT, RR, risk) to this deterministic state transition.
    """
    preplan = str(preplan_status or "WATCH").strip().upper()
    plan = str(plan_status or "REJECT").strip().upper()
    reason = str(plan_reason or "").strip().upper()
    serious = {
        "INVALID_STOP", "LIQUIDITY_VERY_POOR", "ENTRY_HARD_BLOCKER",
        "PRICE_EXTENDED_HARD", "NO_VALID_RESISTANCE_PATH",
        "RISK_REWARD_BELOW_MINIMUM",
    }

    if preplan == "AVOID":
        final_status, execution_status = "AVOID", "NOT READY"
    elif plan == "REJECT" and reason in serious:
        final_status, execution_status = "AVOID", "NOT READY"
    elif preplan == "BUY ON TRIGGER" and plan == "ACCEPT":
        final_status, execution_status = "BUY READY", "BUY CONFIRMED"
    elif preplan == "BUY ON TRIGGER" and plan == "CONDITIONAL":
        final_status, execution_status = "BUY ON TRIGGER", "WAIT TRIGGER"
    elif preplan == "BUY READY" and plan == "ACCEPT":
        final_status, execution_status = "BUY READY", "BUY CONFIRMED"
    else:
        final_status, execution_status = "WATCH", "MONITOR"

    rejected = _json_list(rejected_by_preplan)
    if plan == "ACCEPT":
        # ENTRY_NOT_TRIGGERED is a transient pre-plan state, not a rejection
        # after the entry plan has been accepted.
        rejected = [item for item in rejected if item != "ENTRY_NOT_TRIGGERED"]
    if reason:
        rejected.append(reason)
    rejected = list(dict.fromkeys(item for item in rejected if item))

    trace = _json_list(decision_trace_preplan)
    trace.extend([
        f"PLAN_STATUS={plan}",
        f"PLAN_REASON={reason or 'NONE'}",
        f"RR_MAJOR={major_rr:.2f}" if major_rr is not None and math.isfinite(major_rr) else "RR_MAJOR=NA",
        f"RISK_PCT={risk_pct:.2f}" if risk_pct is not None and math.isfinite(risk_pct) else "RISK_PCT=NA",
        f"FINAL_STATUS={final_status}",
        "FINAL_OWNER=DECISION_ENGINE",
    ])
    return {
        "Decision_Status_Final": final_status,
        "Execution_Status": execution_status,
        "Rejected_By": json.dumps(rejected, ensure_ascii=False),
        "Decision_Trace": json.dumps(trace, ensure_ascii=False),
        "Final_Decision_Owner": "DECISION_ENGINE",
    }
