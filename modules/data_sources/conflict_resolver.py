from __future__ import annotations

"""Per-field conflict resolver for canonical records.

Conflicts are resolved field by field, never row by row, so a single divergent
field does not discard an otherwise agreeing record.  For each contested field
the resolver records the selected value, the source it came from, all candidate
values, the numeric difference, the tolerance applied, a resolution reason, and
a severity.

Key rules from the Stage 3 brief:

* Suspend / trading-status conflicts fail closed.
* Corporate actions are checked before an OHLC conflict is declared (a legit
  split/dividend gap is not a data conflict).
* Fallback usage is always flagged on the winning record.
* Records with different market dates are never merged and never select a
  source-priority winner.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from modules.data_sources import constants as C
from modules.data_sources.canonical import CanonicalRecord


@dataclass
class FieldResolution:
    field_name: str
    selected_value: Any
    selected_source: str
    candidate_values: list[dict[str, Any]]
    difference: float | None
    tolerance: float
    resolution_reason: str
    conflict_severity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "selected_value": self.selected_value,
            "selected_source": self.selected_source,
            "candidate_values": self.candidate_values,
            "difference": self.difference,
            "tolerance": self.tolerance,
            "resolution_reason": self.resolution_reason,
            "conflict_severity": self.conflict_severity,
        }


@dataclass
class ResolutionResult:
    record: CanonicalRecord | None
    conflict_status: str
    fail_closed: bool
    field_resolutions: list[FieldResolution] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_status": self.conflict_status,
            "fail_closed": self.fail_closed,
            "reason": self.reason,
            "field_resolutions": [fr.to_dict() for fr in self.field_resolutions],
        }


def _priority(source_name: str, priorities: dict[str, int]) -> int:
    return priorities.get(source_name, 10_000)


def _numeric_difference(values: Sequence[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(nums) < 2:
        return None
    return max(nums) - min(nums)


class ConflictResolver:
    def __init__(
        self,
        *,
        mode: str = C.MODE_PRIMARY_WITH_FALLBACK,
        source_priorities: dict[str, int] | None = None,
        numeric_tolerance_pct: float = 0.005,
        corporate_action_lookup: Callable[[str, str], bool] | None = None,
    ) -> None:
        if mode not in C.ALL_RESOLVER_MODES:
            raise ValueError(f"RESOLVER_MODE_INVALID: {mode}")
        self.mode = mode
        self.source_priorities = source_priorities or {}
        self.numeric_tolerance_pct = float(numeric_tolerance_pct)
        # Returns True if a corporate action explains an OHLC gap for
        # (symbol, market_date). Injected so this module stays decoupled.
        self.corporate_action_lookup = corporate_action_lookup

    def resolve(self, candidates: list[CanonicalRecord]) -> ResolutionResult:
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return ResolutionResult(None, C.CONFLICT_NONE, False, reason="NO_CANDIDATES")
        if len(candidates) == 1:
            only = candidates[0]
            return ResolutionResult(only, C.CONFLICT_NONE, False, reason="SINGLE_SOURCE")

        # Different market dates are incomparable facts.  Selecting a
        # source-priority winner would silently choose a trading session, so
        # the only valid production behavior is no record + fail closed.
        dates = {str(c.market_date) for c in candidates}
        if len(dates) > 1:
            for candidate in candidates:
                candidate.conflict_status = C.CONFLICT_FAIL_CLOSED
                candidate.quality_status = C.QUALITY_REJECTED
                candidate.quality_reasons = sorted(
                    set(candidate.quality_reasons) | {C.SOURCE_CONFLICT}
                )
            return ResolutionResult(
                None,
                C.CONFLICT_FAIL_CLOSED,
                True,
                reason=f"MARKET_DATE_MISMATCH_FAIL_CLOSED: {sorted(dates)}",
            )

        rtype = candidates[0].record_type
        # Trading status conflicts fail closed.
        if rtype == "TradingStatus":
            return self._resolve_trading_status(candidates)

        winner = self._by_priority(candidates)
        resolutions: list[FieldResolution] = []
        worst_severity = C.SEVERITY_NONE

        for name in winner.domain_fields():
            values = [(c.source, getattr(c, name, None)) for c in candidates]
            present = [(src, val) for src, val in values if val not in (None, "")]
            distinct = {val for _, val in present}
            if len(distinct) <= 1:
                continue  # agreement (or all-missing) — not a conflict

            resolution = self._resolve_field(name, winner, present, rtype)
            resolutions.append(resolution)
            worst_severity = _max_severity(worst_severity, resolution.conflict_severity)

        if not resolutions:
            winner.conflict_status = C.CONFLICT_NONE
            return ResolutionResult(winner, C.CONFLICT_NONE, False, resolutions, "AGREEMENT")

        winner.conflict_status = C.CONFLICT_RESOLVED
        if C.SOURCE_CONFLICT not in winner.quality_reasons:
            winner.quality_reasons = sorted(set(winner.quality_reasons) | {C.SOURCE_CONFLICT})
        return ResolutionResult(
            winner,
            C.CONFLICT_RESOLVED,
            False,
            resolutions,
            reason=f"RESOLVED_{worst_severity}",
        )

    # -- helpers -----------------------------------------------------------
    def _by_priority(self, candidates: list[CanonicalRecord]) -> CanonicalRecord:
        ordered = sorted(candidates, key=lambda c: _priority(c.source, self.source_priorities))
        primary = ordered[0]
        # In PRIMARY_ONLY the fallback candidates are ignored entirely.
        if self.mode == C.MODE_PRIMARY_ONLY:
            return primary
        return primary

    def _resolve_field(
        self,
        name: str,
        winner: CanonicalRecord,
        present: list[tuple[str, Any]],
        rtype: str,
    ) -> FieldResolution:
        candidate_values = [{"source": src, "value": val} for src, val in present]
        numeric_vals = [val for _, val in present]
        diff = _numeric_difference(numeric_vals)

        # Compute tolerance for numeric fields as pct of the winning magnitude.
        winner_val = getattr(winner, name, None)
        tolerance = 0.0
        within_tolerance = False
        if isinstance(winner_val, (int, float)) and not isinstance(winner_val, bool) and diff is not None:
            tolerance = abs(float(winner_val)) * self.numeric_tolerance_pct
            within_tolerance = diff <= tolerance

        # Corporate action check before declaring an OHLC conflict.
        is_ohlc = rtype in {"DailyBar", "MarketIndex"} and name in {"open", "high", "low", "close", "adjusted_close"}
        if is_ohlc and self.corporate_action_lookup is not None:
            if self.corporate_action_lookup(winner.symbol, str(winner.market_date)):
                return FieldResolution(
                    field_name=name,
                    selected_value=winner_val,
                    selected_source=winner.source,
                    candidate_values=candidate_values,
                    difference=diff,
                    tolerance=tolerance,
                    resolution_reason="CORPORATE_ACTION_EXPLAINS_GAP",
                    conflict_severity=C.SEVERITY_LOW,
                )

        if within_tolerance:
            reason = "WITHIN_TOLERANCE_PRIMARY_KEPT"
            severity = C.SEVERITY_LOW
        else:
            reason = "PRIMARY_PRIORITY_SELECTED"
            severity = C.SEVERITY_MEDIUM if diff is not None else C.SEVERITY_MEDIUM

        # CONSENSUS mode: for numeric fields pick the median of candidates.
        if self.mode == C.MODE_CONSENSUS and diff is not None:
            nums = sorted(
                float(v) for v in numeric_vals
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            )
            median = nums[len(nums) // 2] if len(nums) % 2 else (nums[len(nums) // 2 - 1] + nums[len(nums) // 2]) / 2
            setattr(winner, name, median)
            reason = "CONSENSUS_MEDIAN"

        return FieldResolution(
            field_name=name,
            selected_value=getattr(winner, name, None),
            selected_source=winner.source,
            candidate_values=candidate_values,
            difference=diff,
            tolerance=tolerance,
            resolution_reason=reason,
            conflict_severity=severity,
        )

    def _resolve_trading_status(self, candidates: list[CanonicalRecord]) -> ResolutionResult:
        statuses = {str(getattr(c, "status", "")).upper() for c in candidates}
        suspended = {bool(getattr(c, "is_suspended", False)) for c in candidates}
        ambiguous = any(getattr(c, "ambiguous", False) for c in candidates)
        winner = self._by_priority(candidates)

        if ambiguous or len(statuses) > 1 or len(suspended) > 1:
            # Fail closed: mark not tradable and flag ambiguity.
            winner.status = "SUSPEND"
            winner.is_tradable = False
            winner.is_suspended = True
            winner.ambiguous = True
            winner.conflict_status = C.CONFLICT_FAIL_CLOSED
            winner.quality_reasons = sorted(
                set(winner.quality_reasons) | {C.SUSPEND_STATUS_AMBIGUOUS, C.SOURCE_CONFLICT}
            )
            resolution = FieldResolution(
                field_name="status",
                selected_value=winner.status,
                selected_source=winner.source,
                candidate_values=[{"source": c.source, "value": getattr(c, "status", "")} for c in candidates],
                difference=None,
                tolerance=0.0,
                resolution_reason="SUSPEND_AMBIGUOUS_FAIL_CLOSED",
                conflict_severity=C.SEVERITY_CRITICAL,
            )
            return ResolutionResult(
                winner, C.CONFLICT_FAIL_CLOSED, True, [resolution], "SUSPEND_AMBIGUOUS_FAIL_CLOSED"
            )

        winner.conflict_status = C.CONFLICT_NONE
        return ResolutionResult(winner, C.CONFLICT_NONE, False, [], "STATUS_AGREEMENT")


_SEVERITY_RANK = {
    C.SEVERITY_NONE: 0,
    C.SEVERITY_LOW: 1,
    C.SEVERITY_MEDIUM: 2,
    C.SEVERITY_HIGH: 3,
    C.SEVERITY_CRITICAL: 4,
}


def _max_severity(a: str, b: str) -> str:
    return a if _SEVERITY_RANK.get(a, 0) >= _SEVERITY_RANK.get(b, 0) else b
