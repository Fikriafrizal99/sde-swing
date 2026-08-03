#!/usr/bin/env python3
"""Entry plan state transition without re-scoring stock quality.

The Final Decision Engine owns setup quality.  This validator only consumes
execution facts (trigger, zone, stop, resistance, RR, risk, liquidity/regime
conditions) and maps the pre-plan status into the canonical final status.
"""
from __future__ import annotations

import json
import math
from typing import Any, Iterable

SERIOUS_PLAN_BLOCKERS = {
    "INVALID_DATA",
    "INVALID_STOP",
    "LIQUIDITY_VERY_POOR",
    "ENTRY_HARD_BLOCKER",
    "PRICE_EXTENDED_HARD",
    "NO_VALID_RESISTANCE_PATH",
    "RISK_REWARD_BELOW_MINIMUM",
    "NOT_TRADEABLE",
    "SUSPENDED",
}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return [str(item) for item in decoded if str(item).strip()]
        except Exception:
            return []
    return []


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def finalize_entry_plan(
    *,
    preplan_status: str,
    plan_status: str,
    plan_reason: str,
    rejected_by_preplan: Any = None,
    decision_trace_preplan: Any = None,
    major_rr: float | None = None,
    risk_pct: float | None = None,
    trigger_confirmed: bool | None = None,
    conditional_reasons: Iterable[str] | None = None,
    position_size_multiplier: float = 1.0,
) -> dict[str, Any]:
    """Finalize execution readiness while preserving Final Decision ownership.

    `WATCH` is never promoted by an attractive entry plan.  `BUY ON TRIGGER`
    becomes `BUY READY` only when the plan is accepted and the trigger is not
    explicitly false.  Low readiness/conditional context remains a timing
    condition instead of becoming a quality rejection.
    """
    preplan = str(preplan_status or "WATCH").strip().upper()
    plan = str(plan_status or "CONDITIONAL").strip().upper()
    reason = str(plan_reason or "").strip().upper()
    conditions = _unique(conditional_reasons or [])
    serious = reason in SERIOUS_PLAN_BLOCKERS or any(item in SERIOUS_PLAN_BLOCKERS for item in conditions)

    if preplan == "AVOID":
        final_status, execution_status = "AVOID", "NOT READY"
    elif serious or plan == "REJECT":
        # A generic low-readiness reason is not a serious blocker.  It is mapped
        # to BUY ON TRIGGER below rather than broadening the AVOID population.
        if reason in {"ENTRY_READINESS_BELOW_MINIMUM", "WAIT_FOR_ENTRY_TRIGGER", "ENTRY_NOT_TRIGGERED"} and preplan == "BUY ON TRIGGER":
            final_status, execution_status = "BUY ON TRIGGER", "WAIT TRIGGER"
        else:
            final_status, execution_status = "AVOID", "NOT READY"
    elif preplan == "BUY ON TRIGGER" and plan == "ACCEPT" and trigger_confirmed is not False:
        final_status, execution_status = "BUY READY", "BUY CONFIRMED"
    elif preplan in {"BUY ON TRIGGER", "BUY READY"}:
        final_status, execution_status = "BUY ON TRIGGER", "WAIT TRIGGER"
    else:
        final_status, execution_status = "WATCH", "MONITOR"

    rejected = _json_list(rejected_by_preplan)
    if final_status == "BUY READY":
        rejected = [item for item in rejected if item not in {"ENTRY_NOT_TRIGGERED", "WAIT_FOR_ENTRY_TRIGGER"}]
    if reason:
        rejected.append(reason)
    rejected.extend(conditions)
    rejected = _unique(rejected)

    trace = _json_list(decision_trace_preplan)
    trace.extend([
        f"PLAN_STATUS={plan}",
        f"PLAN_REASON={reason or 'NONE'}",
        f"TRIGGER_CONFIRMED={str(trigger_confirmed).upper() if trigger_confirmed is not None else 'UNKNOWN'}",
        f"RR_MAJOR={major_rr:.2f}" if major_rr is not None and math.isfinite(major_rr) else "RR_MAJOR=NA",
        f"RISK_PCT={risk_pct:.2f}" if risk_pct is not None and math.isfinite(risk_pct) else "RISK_PCT=NA",
        f"POSITION_MULTIPLIER={max(0.0, min(float(position_size_multiplier), 1.0)):.2f}",
        f"FINAL_STATUS={final_status}",
        "FINAL_OWNER=DECISION_ENGINE",
    ])
    return {
        "Decision_Status_Final": final_status,
        "Execution_Status": execution_status,
        "Rejected_By": json.dumps(rejected, ensure_ascii=False),
        "Decision_Trace": json.dumps(trace, ensure_ascii=False),
        "Final_Decision_Owner": "DECISION_ENGINE",
        "Entry_Validator_Owner": "ENTRY_PLAN_VALIDATOR",
        "Position_Size_Multiplier": round(max(0.0, min(float(position_size_multiplier), 1.0)), 4),
    }
