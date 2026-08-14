from __future__ import annotations

"""Replay-fidelity contract for historical lifecycle evaluation.

The canonical historical evaluators always reproduce the price lifecycle.
They must not imply that live-only broker, decision, or contextual signals were
replayed unless those inputs were both available and actually applied.
"""

from collections.abc import Iterable, Mapping
from typing import Any


REPLAY_CONTRACT_VERSION = "SDE_SWING_REPLAY_V1"

PRICE_REPRODUCIBLE_LIFECYCLE = "PRICE_REPRODUCIBLE_LIFECYCLE"
PRICE_DERIVED_CONTEXT = "PRICE_DERIVED_CONTEXT"
RUNTIME_CONTEXT_OVERLAY = "RUNTIME_CONTEXT_OVERLAY"

PRICE_LIFECYCLE_ONLY = "PRICE_LIFECYCLE_ONLY"
CONTEXT_ENRICHED_PARTIAL_REPLAY = "CONTEXT_ENRICHED_PARTIAL_REPLAY"
FULL_LIVE_REPLAY = "FULL_LIVE_REPLAY"

EXPECTED_CONTEXTUAL_DIVERGENCE = "EXPECTED_CONTEXTUAL_DIVERGENCE"
NO_DIVERGENCE_EXPECTED_WITHIN_DECLARED_SCOPE = (
    "NO_DIVERGENCE_EXPECTED_WITHIN_DECLARED_SCOPE"
)

PRICE_LIFECYCLE_COMPONENTS = (
    "STOP_LOSS",
    "TP1_MILESTONE_TRAILING_ACTIVE",
    "TP2_FULL_CLOSE",
    "BREAKEVEN_AFTER_1R",
    "TRAILING_AFTER_1_5R",
    "MAX_HOLD",
    "SAME_CANDLE_STOP_PRIORITY",
)

PRICE_DERIVED_CONTEXT_COMPONENTS = (
    "CLOSE_BELOW_EMA20_RUNTIME_EXIT",
)

RUNTIME_CONTEXT_COMPONENTS = (
    "BROKER_DISTRIBUTION",
    "DECISION_DOWNGRADE",
    "CONTEXTUAL_RUNTIME_EXIT",
)


def _family_payload(
    components: tuple[str, ...],
    *,
    available: bool,
    applied: bool,
) -> dict[str, Any]:
    return {
        "available": bool(available),
        "applied": bool(applied),
        "components": list(components),
    }


def build_replay_metadata(
    *,
    runtime_context_available: bool = False,
    runtime_context_applied: bool = False,
    price_derived_context_available: bool = False,
    price_derived_context_applied: bool = False,
) -> dict[str, Any]:
    """Build auditable replay metadata without inventing missing context."""

    if runtime_context_applied and not runtime_context_available:
        raise ValueError("runtime context cannot be applied when it is unavailable")
    if price_derived_context_applied and not price_derived_context_available:
        raise ValueError("price-derived context cannot be applied when it is unavailable")

    applied_scopes = [PRICE_REPRODUCIBLE_LIFECYCLE]
    if price_derived_context_applied:
        applied_scopes.append(PRICE_DERIVED_CONTEXT)
    if runtime_context_applied:
        applied_scopes.append(RUNTIME_CONTEXT_OVERLAY)

    full_live_replay = bool(
        price_derived_context_available
        and price_derived_context_applied
        and runtime_context_available
        and runtime_context_applied
    )
    if full_live_replay:
        replay_fidelity = FULL_LIVE_REPLAY
    elif len(applied_scopes) > 1:
        replay_fidelity = CONTEXT_ENRICHED_PARTIAL_REPLAY
    else:
        replay_fidelity = PRICE_LIFECYCLE_ONLY

    families = {
        PRICE_REPRODUCIBLE_LIFECYCLE: _family_payload(
            PRICE_LIFECYCLE_COMPONENTS,
            available=True,
            applied=True,
        ),
        PRICE_DERIVED_CONTEXT: _family_payload(
            PRICE_DERIVED_CONTEXT_COMPONENTS,
            available=price_derived_context_available,
            applied=price_derived_context_applied,
        ),
        RUNTIME_CONTEXT_OVERLAY: _family_payload(
            RUNTIME_CONTEXT_COMPONENTS,
            available=runtime_context_available,
            applied=runtime_context_applied,
        ),
    }
    excluded = [name for name, state in families.items() if not state["applied"]]
    unavailable = [name for name, state in families.items() if not state["available"]]

    return {
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "replay_scope": "+".join(applied_scopes),
        "replay_scopes": applied_scopes,
        "replay_fidelity": replay_fidelity,
        "runtime_context_available": bool(runtime_context_available),
        "runtime_context_applied": bool(runtime_context_applied),
        "price_derived_context_available": bool(price_derived_context_available),
        "price_derived_context_applied": bool(price_derived_context_applied),
        "full_live_replay": full_live_replay,
        "divergence_classification": (
            NO_DIVERGENCE_EXPECTED_WITHIN_DECLARED_SCOPE
            if full_live_replay
            else EXPECTED_CONTEXTUAL_DIVERGENCE
        ),
        "included_exit_families": applied_scopes,
        "excluded_exit_families": excluded,
        "unavailable_exit_families": unavailable,
        "exit_families": families,
    }


def historical_replay_metadata() -> dict[str, Any]:
    """Default contract for current backtest and performance evaluators."""

    return build_replay_metadata()


def replay_report_columns(
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten the contract fields that must travel with CSV report rows."""

    replay = dict(metadata or historical_replay_metadata())
    return {
        "Replay_Contract_Version": replay["replay_contract_version"],
        "Replay_Scope": replay["replay_scope"],
        "Replay_Fidelity": replay["replay_fidelity"],
        "Runtime_Context_Available": replay["runtime_context_available"],
        "Runtime_Context_Applied": replay["runtime_context_applied"],
        "Price_Derived_Context_Available": replay[
            "price_derived_context_available"
        ],
        "Price_Derived_Context_Applied": replay["price_derived_context_applied"],
        "Full_Live_Replay": replay["full_live_replay"],
        "Replay_Divergence_Classification": replay[
            "divergence_classification"
        ],
    }


def _reason_tokens(reasons: str | Iterable[str]) -> list[str]:
    values = [reasons] if isinstance(reasons, str) else list(reasons)
    tokens: list[str] = []
    for value in values:
        tokens.extend(part.strip().upper() for part in str(value).split("|") if part.strip())
    return tokens


def classify_exit_reasons(reasons: str | Iterable[str]) -> dict[str, list[str]]:
    """Classify observed exit reasons into replay-contract families."""

    classified = {
        PRICE_REPRODUCIBLE_LIFECYCLE: [],
        PRICE_DERIVED_CONTEXT: [],
        RUNTIME_CONTEXT_OVERLAY: [],
    }
    price_reasons = {
        "STOP_LOSS",
        "STOP_LOSS_HIT",
        "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE",
        "AMBIGUOUS_BAR_STOP_PRIORITY",
        "TARGET_1",
        "TARGET_1_HIT_TRAIL_ACTIVE",
        "TP1_HIT",
        "TARGET_2",
        "TP2_HIT",
        "MAX_HOLD_EXIT",
    }
    for reason in _reason_tokens(reasons):
        if reason == "CLOSE_BELOW_EMA20":
            family = PRICE_DERIVED_CONTEXT
        elif reason.startswith("BROKER_DISTRIBUTION") or reason.startswith(
            "DECISION_DOWNGRADE_"
        ):
            family = RUNTIME_CONTEXT_OVERLAY
        elif reason in price_reasons or reason.startswith("MAX_HOLD_"):
            family = PRICE_REPRODUCIBLE_LIFECYCLE
        else:
            # Unknown live context is conservative: historical price-only
            # evaluation must not silently claim it reproduced the signal.
            family = RUNTIME_CONTEXT_OVERLAY
        classified[family].append(reason)
    return classified
