from __future__ import annotations

"""Decision Engine integration bridge (section S) and shadow comparison (T).

The bridge attaches broker multi-day CONTEXT to decision rows.  It never
computes or overrides Decision_Status_Final and never changes Stage-2
thresholds.  Hierarchy: Technical is the primary engine, broker daily is
timing, broker multi-day is confirmation context only.

Shadow comparison evaluates alternative broker-window framings side by side so
an operator can compare them — without any of them becoming production.
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd

from modules.data_sources.broker_multiday_engine import (
    MultiDayContext,
    compute_multiday_context,
)

# Shadow framings to compare (section T).
SHADOW_FRAMINGS = (
    "DAILY_ONLY",
    "PRIMARY_3D",
    "PRIMARY_5D",
    "PRIMARY_10D",
    "PRIMARY_20D",
    "MULTI_WINDOW_CONSENSUS",
)

# Context columns the bridge is allowed to add. Decision_Status_Final and every
# Stage-2 scoring column are intentionally absent.
CONTEXT_COLUMNS = (
    "Broker_MultiDay_Score",
    "Broker_MultiDay_Confidence",
    "Broker_MultiDay_Penalty",
    "Broker_MultiDay_Blocker",
    "Broker_MultiDay_Context",
    "Broker_MultiDay_Primary_Window",
    "Broker_Context_1D",
    "Broker_Context_3D",
    "Broker_Context_5D",
    "Broker_Context_10D",
    "Broker_Context_20D",
    "Broker_Context_Primary",
    "Broker_Context_Confidence",
    "Broker_Context_Alignment",
    "Broker_MultiDay_Trace",
)

PROTECTED_COLUMNS = frozenset({
    "Decision_Status_Final",
    "Decision_Status",
    "Decision_Status_PrePlan",
    "Final_Score_V3",
    "Decision_V3",
})


@dataclass
class BridgeResult:
    frame: pd.DataFrame
    contexts: dict[str, MultiDayContext]
    protected_intact: bool

    def to_manifest(self) -> dict[str, Any]:
        return {
            "rows": int(len(self.frame)),
            "symbols_with_context": len(self.contexts),
            "context_columns_added": list(CONTEXT_COLUMNS),
            "protected_columns_untouched": self.protected_intact,
        }


def attach_multiday_context(
    decision_frame: pd.DataFrame,
    context_by_symbol: dict[str, MultiDayContext],
    *,
    symbol_col: str = "Symbol",
) -> BridgeResult:
    """Left-join multi-day context onto decision rows without touching scores."""
    before = {
        c: decision_frame[c].copy() for c in PROTECTED_COLUMNS if c in decision_frame.columns
    }
    out = decision_frame.copy()

    for col in CONTEXT_COLUMNS:
        out[col] = None
    if symbol_col not in out.columns:
        return BridgeResult(out, context_by_symbol, True)

    for idx, row in out.iterrows():
        symbol = str(row.get(symbol_col, "")).strip().upper()
        ctx = context_by_symbol.get(symbol)
        if ctx is None:
            continue
        ctx_dict = ctx.to_context_dict()
        for col in CONTEXT_COLUMNS:
            if col in ctx_dict:
                out.at[idx, col] = ctx_dict[col]

    intact = True
    for col, series in before.items():
        if not out[col].equals(series):
            intact = False
            out[col] = series
    return BridgeResult(out, context_by_symbol, intact)


def _global_market_date(broker_rows_by_symbol: dict[str, list[dict[str, Any]]]) -> str:
    """Return one common as-of session for every symbol in the same dataset."""
    return max(
        (
            str(row.get("market_date", ""))
            for rows in broker_rows_by_symbol.values()
            for row in rows
            if str(row.get("market_date", "")).strip()
        ),
        default="",
    )


def build_contexts_for_symbols(
    broker_rows_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    primary_window: str = "5D",
    current_price_by_symbol: dict[str, float] | None = None,
    returns_by_symbol: dict[str, dict[str, float]] | None = None,
    aggregate_foreign_by_symbol: dict[str, float] | None = None,
) -> dict[str, MultiDayContext]:
    current_price_by_symbol = current_price_by_symbol or {}
    returns_by_symbol = returns_by_symbol or {}
    aggregate_foreign_by_symbol = aggregate_foreign_by_symbol or {}
    out: dict[str, MultiDayContext] = {}
    # Every symbol must be evaluated against the same latest market session.
    # Otherwise a symbol missing today's broker row would silently move its
    # 3D/5D window backward and appear fully covered.
    market_date = _global_market_date(broker_rows_by_symbol)
    for symbol, rows in broker_rows_by_symbol.items():
        out[symbol] = compute_multiday_context(
            symbol,
            market_date,
            rows,
            primary_window=primary_window,
            current_price=current_price_by_symbol.get(symbol),
            window_returns_pct=returns_by_symbol.get(symbol),
            aggregate_foreign_net=aggregate_foreign_by_symbol.get(symbol),
        )
    return out


# ---------------------------------------------------------------------------
# T. Shadow comparison
# ---------------------------------------------------------------------------
@dataclass
class ShadowFramingResult:
    framing: str
    primary_window: str | None
    context_counts: dict[str, int]
    blocker_count: int
    average_confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "framing": self.framing,
            "primary_window": self.primary_window,
            "context_counts": self.context_counts,
            "blocker_count": self.blocker_count,
            "average_confidence": round(self.average_confidence, 2),
        }


def _framing_window(framing: str) -> str | None:
    return {
        "DAILY_ONLY": "1D",
        "PRIMARY_3D": "3D",
        "PRIMARY_5D": "5D",
        "PRIMARY_10D": "10D",
        "PRIMARY_20D": "20D",
        "MULTI_WINDOW_CONSENSUS": None,
    }.get(framing)


def run_shadow_comparison(
    broker_rows_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    current_price_by_symbol: dict[str, float] | None = None,
    returns_by_symbol: dict[str, dict[str, float]] | None = None,
) -> dict[str, ShadowFramingResult]:
    """Compare broker-window framings on the SAME technical + raw inputs."""
    results: dict[str, ShadowFramingResult] = {}
    market_date = _global_market_date(broker_rows_by_symbol)
    for framing in SHADOW_FRAMINGS:
        window = _framing_window(framing)
        counts: dict[str, int] = {}
        blockers = 0
        confidences: list[float] = []
        for symbol, rows in broker_rows_by_symbol.items():
            if window is not None:
                ctx = compute_multiday_context(
                    symbol, market_date, rows,
                    primary_window=window,
                    current_price=(current_price_by_symbol or {}).get(symbol),
                    window_returns_pct=(returns_by_symbol or {}).get(symbol),
                )
                label = ctx.broker_multiday_context
                confidences.append(ctx.broker_multiday_confidence)
                blockers += 1 if ctx.broker_multiday_blocker else 0
            else:
                ctx = compute_multiday_context(
                    symbol, market_date, rows,
                    primary_window="5D",
                    current_price=(current_price_by_symbol or {}).get(symbol),
                    window_returns_pct=(returns_by_symbol or {}).get(symbol),
                )
                labels = [c.classification for c in ctx.classifications.values()]
                label = _majority(labels)
                confidences.append(ctx.broker_multiday_confidence)
                blockers += 1 if ctx.broker_multiday_blocker else 0
            counts[label] = counts.get(label, 0) + 1
        results[framing] = ShadowFramingResult(
            framing=framing,
            primary_window=window,
            context_counts=counts,
            blocker_count=blockers,
            average_confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        )
    return results


def _majority(labels: list[str]) -> str:
    if not labels:
        return "INSUFFICIENT_DATA"
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]
