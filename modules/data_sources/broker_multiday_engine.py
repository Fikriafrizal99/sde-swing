from __future__ import annotations

"""Broker multi-day context engine (section S).

Orchestrates: broker history -> windows -> features -> classification ->
persistence -> acceleration -> divergence -> alignment.

CRITICAL CONTRACT: this engine never produces BUY / WATCH / AVOID.  It returns
only score, context, confidence, penalty, blocker, and trace.  The Final
Decision Engine remains the sole owner of Decision_Status_Final.  Technical is
the primary engine; broker daily is timing; broker multi-day is confirmation
context only.
"""

from dataclasses import dataclass, field
from typing import Any

from modules.data_sources.broker_analytics import (
    AlignmentResult,
    ClassificationResult,
    check_foreign_double_count,
    classify_window,
    compute_acceleration_across_windows,
    compute_alignment,
    compute_divergence,
    compute_persistence,
)
from modules.data_sources.broker_windows import (
    WINDOWS,
    BrokerDay,
    WindowFeatures,
    compute_window_features,
)


@dataclass
class MultiDayContext:
    symbol: str
    market_date: str
    primary_window: str
    windows: dict[str, WindowFeatures]
    classifications: dict[str, ClassificationResult]
    alignment: AlignmentResult
    acceleration: dict[str, Any]
    divergence: dict[str, Any]
    persistence: dict[str, Any]
    foreign_protection: dict[str, Any]
    # The single context bundle the Decision Engine consumes.
    broker_multiday_score: float
    broker_multiday_confidence: float
    broker_multiday_penalty: float
    broker_multiday_blocker: bool
    broker_multiday_context: str
    trace: list[str] = field(default_factory=list)

    def to_context_dict(self) -> dict[str, Any]:
        """The opaque context the Decision Engine ingests. No BUY/WATCH/AVOID."""
        out: dict[str, Any] = {
            "Broker_MultiDay_Score": round(self.broker_multiday_score, 4),
            "Broker_MultiDay_Confidence": round(self.broker_multiday_confidence, 2),
            "Broker_MultiDay_Penalty": round(self.broker_multiday_penalty, 2),
            "Broker_MultiDay_Blocker": self.broker_multiday_blocker,
            "Broker_MultiDay_Context": self.broker_multiday_context,
            "Broker_MultiDay_Primary_Window": self.primary_window,
            "Broker_MultiDay_Trace": " | ".join(self.trace),
        }
        out.update(self.alignment.to_dict())
        out.update(self.acceleration)
        out.update(self.divergence)
        out.update(self.persistence)
        out.update(self.foreign_protection)
        return out

    def to_detail_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market_date": self.market_date,
            "primary_window": self.primary_window,
            "windows": {w: f.to_dict() for w, f in self.windows.items()},
            "classifications": {w: c.to_dict() for w, c in self.classifications.items()},
            **self.to_context_dict(),
        }


def build_broker_days(rows: list[dict[str, Any]]) -> list[BrokerDay]:
    """Group broker_daily rows by market_date into BrokerDay objects."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_date.setdefault(str(r.get("market_date")), []).append(r)
    return [BrokerDay(market_date=d, rows=rows) for d, rows in sorted(by_date.items())]


def compute_multiday_context(
    symbol: str,
    market_date: str,
    broker_rows: list[dict[str, Any]],
    *,
    primary_window: str = "5D",
    current_price: float | None = None,
    window_returns_pct: dict[str, float] | None = None,
    aggregate_foreign_net: float | None = None,
) -> MultiDayContext:
    days = build_broker_days(broker_rows)
    window_returns_pct = window_returns_pct or {}

    windows: dict[str, WindowFeatures] = {}
    classifications: dict[str, ClassificationResult] = {}
    for w in WINDOWS:
        wf = compute_window_features(days, w, current_price=current_price)
        windows[w] = wf
        classifications[w] = classify_window(wf)

    alignment = compute_alignment(classifications, primary_window)
    acceleration = compute_acceleration_across_windows(windows).to_dict()

    primary_wf = windows.get(primary_window) or next(iter(windows.values()))
    primary_cls = classifications.get(primary_window)
    divergence = compute_divergence(
        primary_wf, window_returns_pct.get(primary_window)
    ).to_dict()

    # Persistence: compare short window (3D) vs primary (5D by default).
    short_wf = windows.get("3D", primary_wf)
    persistence = compute_persistence(short_wf, primary_wf).to_dict()

    # Foreign double-count protection using primary-window foreign net.
    foreign_net = sum(
        (r.get("net_value") or 0.0)
        for r in primary_wf_rows(days, primary_window)
        if str(r.get("broker_type", "")).upper() == "ASING"
    )
    foreign_protection = check_foreign_double_count(
        broker_flow_foreign_net=float(foreign_net),
        aggregate_foreign_net=aggregate_foreign_net,
        flow_origin="DERIVED_FROM_BROKER",
    ).to_dict()

    # Aggregate the multi-day context bundle.
    score = primary_cls.score if primary_cls else 0.0
    confidence = primary_cls.confidence if primary_cls else 0.0
    penalty = primary_cls.penalty if primary_cls else 0.0
    blocker = any(c.blocker for c in classifications.values())
    context_label = primary_cls.classification if primary_cls else "INSUFFICIENT_DATA"

    trace: list[str] = []
    trace.append(f"primary={primary_window} score={score:.1f} conf={confidence:.0f}")
    trace.append(f"alignment={alignment.alignment}")
    if blocker:
        trace.append("MULTIDAY_HARD_BLOCKER")
    if foreign_protection.get("Double_Count_Risk"):
        trace.append("FOREIGN_DOUBLE_COUNT_RISK")

    return MultiDayContext(
        symbol=symbol,
        market_date=market_date,
        primary_window=primary_window,
        windows=windows,
        classifications=classifications,
        alignment=alignment,
        acceleration=acceleration,
        divergence=divergence,
        persistence=persistence,
        foreign_protection=foreign_protection,
        broker_multiday_score=score,
        broker_multiday_confidence=confidence,
        broker_multiday_penalty=penalty,
        broker_multiday_blocker=blocker,
        broker_multiday_context=context_label,
        trace=trace,
    )


def primary_wf_rows(days: list[BrokerDay], primary_window: str) -> list[dict[str, Any]]:
    expected = WINDOWS.get(primary_window, 5)
    ordered = sorted(days, key=lambda d: d.market_date)[-expected:]
    return [r for d in ordered for r in d.rows]