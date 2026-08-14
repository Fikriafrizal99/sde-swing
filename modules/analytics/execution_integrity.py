from __future__ import annotations

"""Execution-time integrity helpers for outcome tracking and analytics.

These helpers never recalculate entry, stop, TP1, or TP2. They only validate
whether the existing engine-owned plan is still economically coherent after an
actual trigger price is known.
"""

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanGeometry:
    valid: bool
    status: str
    reasons: tuple[str, ...]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate_plan_geometry(
    entry: Any,
    stop: Any = None,
    tp1: Any = None,
    tp2: Any = None,
) -> PlanGeometry:
    """Validate existing plan levels against the actual execution entry.

    Missing optional levels are not invented. A supplied stop must remain below
    entry; supplied profit targets must remain above entry; and TP2 may not sit
    below TP1. This is an integrity check only, not a target-generation rule.
    """
    entry_value = _number(entry)
    stop_value = _number(stop)
    tp1_value = _number(tp1)
    tp2_value = _number(tp2)

    if entry_value is None or entry_value <= 0:
        return PlanGeometry(False, "INVALID_ENTRY", ("INVALID_ENTRY",))

    reasons: list[str] = []
    if stop_value is not None and stop_value >= entry_value:
        reasons.append("STOP_NOT_BELOW_ENTRY")
    if tp1_value is not None and tp1_value <= entry_value:
        reasons.append("TP1_NOT_ABOVE_ENTRY")
    if tp2_value is not None and tp2_value <= entry_value:
        reasons.append("TP2_NOT_ABOVE_ENTRY")
    if tp1_value is not None and tp2_value is not None and tp2_value < tp1_value:
        reasons.append("TP_ORDER_INVALID")

    if not reasons:
        return PlanGeometry(True, "VALID", ())
    return PlanGeometry(False, "INVALID_" + "+".join(reasons), tuple(reasons))


def target_is_profitable(entry: Any, target: Any) -> bool:
    entry_value = _number(entry)
    target_value = _number(target)
    return bool(
        entry_value is not None
        and target_value is not None
        and entry_value > 0
        and target_value > entry_value
    )


def economic_outcome(realized_return_pct: Any) -> str:
    realized = _number(realized_return_pct)
    if realized is None or abs(realized) < 1e-12:
        return "AMBIGUOUS"
    return "WIN" if realized > 0 else "LOSS"


def outcome_consistency(final_outcome: Any, realized_return_pct: Any) -> str:
    """Audit a stored outcome without mutating it."""
    reported = str(final_outcome or "").strip().upper()
    realized = _number(realized_return_pct)
    if reported not in {"WIN", "LOSS", "AMBIGUOUS"}:
        return "NOT_CLOSED_OUTCOME"
    if realized is None:
        return "REALIZED_RETURN_MISSING"
    economic = economic_outcome(realized)
    if reported == economic:
        return "CONSISTENT"
    return f"REPORTED_{reported}_ECONOMIC_{economic}"
