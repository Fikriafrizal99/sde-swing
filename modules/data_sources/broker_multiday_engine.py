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
    select_window_days,
)
from modules.broker_bridge.broker_period_context import primary_pulse_alignment


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
    broker_multiday_score: float
    broker_multiday_confidence: float
    broker_multiday_penalty: float
    broker_multiday_blocker: bool
    broker_multiday_context: str
    trace: list[str] = field(default_factory=list)
    # The fields below are metadata/context only.  They are deliberately
    # appended after the existing engine fields so legacy constructors and the
    # existing score/confidence calculations remain unchanged.
    period_metadata: dict[str, Any] = field(default_factory=dict)
    today_pulse: dict[str, Any] = field(default_factory=dict)
    broker_period_alignment: str = "INSUFFICIENT"

    def to_context_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "Broker_MultiDay_Score": round(self.broker_multiday_score, 4),
            "Broker_MultiDay_Confidence": round(self.broker_multiday_confidence, 2),
            "Broker_MultiDay_Penalty": round(self.broker_multiday_penalty, 2),
            "Broker_MultiDay_Blocker": self.broker_multiday_blocker,
            "Broker_MultiDay_Context": self.broker_multiday_context,
            "Broker_MultiDay_Primary_Window": self.primary_window,
            "Broker_MultiDay_Trace": " | ".join(self.trace),
        }
        if self.period_metadata:
            out.update(self.period_metadata)
            # Upper-case aliases make CSV/report bridges tolerant of the two
            # conventions already present in the repository.
            out.update({
                "Broker_Period_Type": self.period_metadata.get("broker_period_type", ""),
                "Broker_Period_Start": self.period_metadata.get("broker_period_start", ""),
                "Broker_Period_End": self.period_metadata.get("broker_period_end", ""),
                "Broker_Trading_Days": self.period_metadata.get("broker_trading_days", ""),
                "Broker_Session_Dates": self.period_metadata.get("broker_session_dates", []),
                "Broker_Snapshot_ID": self.period_metadata.get("broker_snapshot_id", ""),
                "Broker_Period_Source": self.period_metadata.get("broker_period_source", ""),
                "Broker_Coverage": self.period_metadata.get("broker_coverage", ""),
                "Broker_Freshness_Status": self.period_metadata.get("broker_freshness_status", ""),
            })
        if self.today_pulse and str(self.period_metadata.get("broker_period_type", "")).upper() not in {"", "1D", "1DAY", "DAY"}:
            out.update(self.today_pulse)
            out["broker_alignment"] = self.broker_period_alignment
            out["Broker_Period_Alignment"] = self.broker_period_alignment
        for window in WINDOWS:
            classification = self.classifications.get(window)
            out[f"Broker_Context_{window}"] = (
                classification.classification if classification else "INSUFFICIENT_DATA"
            )
            if classification is not None:
                out[f"Broker_Score_{window}"] = round(classification.score, 4)
                out[f"Broker_Confidence_{window}"] = round(classification.confidence, 2)
                out[f"Broker_Blocker_{window}"] = bool(classification.blocker)
        if self.primary_window not in WINDOWS:
            classification = self.classifications.get(self.primary_window)
            out[f"Broker_Context_{self.primary_window}"] = (
                classification.classification if classification else "INSUFFICIENT_DATA"
            )
            if classification is not None:
                out[f"Broker_Score_{self.primary_window}"] = round(classification.score, 4)
                out[f"Broker_Confidence_{self.primary_window}"] = round(classification.confidence, 2)
                out[f"Broker_Blocker_{self.primary_window}"] = bool(classification.blocker)
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
            "period_metadata": dict(self.period_metadata),
            "today_pulse": dict(self.today_pulse),
            "broker_period_alignment": self.broker_period_alignment,
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
    period_metadata: dict[str, Any] | None = None,
    today_pulse: dict[str, Any] | None = None,
) -> MultiDayContext:
    days = build_broker_days(broker_rows)
    window_returns_pct = window_returns_pct or {}
    period_metadata = dict(period_metadata or {})
    today_pulse = dict(today_pulse or {})
    period_type = str(period_metadata.get("broker_period_type", "")).upper()
    if period_type not in {"", "1D", "1DAY", "DAY"} and not today_pulse:
        today_pulse = {
            "today_pulse_available": False,
            "today_pulse_date": str(period_metadata.get("broker_period_end", market_date)),
            "today_pulse_snapshot_id": "",
            "today_pulse_source": "",
            "today_pulse_status": "NOT_AVAILABLE",
            "today_pulse_net_flow": 0.0,
            "today_pulse_buy_days": 0,
            "today_pulse_sell_days": 0,
            "today_pulse_direction": "INSUFFICIENT",
        }

    windows: dict[str, WindowFeatures] = {}
    classifications: dict[str, ClassificationResult] = {}
    for w in WINDOWS:
        # Every window is anchored to the engine's market_date.  A symbol with
        # missing 08-Aug data must not silently shift its 3D window backward.
        wf = compute_window_features(
            days,
            w,
            current_price=current_price,
            as_of_date=market_date,
        )
        windows[w] = wf
        classifications[w] = classify_window(wf)

    # CUSTOM is an explicit IDX-session window, not a synthetic daily split.
    # Reuse the exact existing feature/classification formula with an explicit
    # session list, while keeping the legacy fixed-window output unchanged.
    if primary_window == "CUSTOM":
        custom_dates = [
            str(value)[:10]
            for value in period_metadata.get("broker_session_dates", []) or []
            if str(value).strip()
        ]
        custom_count = int(
            period_metadata.get("broker_trading_days", len(custom_dates))
            or len(custom_dates)
            or len(days)
        )
        if custom_count > 0:
            custom_features = compute_window_features(
                days,
                "CUSTOM",
                current_price=current_price,
                as_of_date=market_date,
                session_count=custom_count,
                expected_dates_override=custom_dates or None,
            )
            windows["CUSTOM"] = custom_features
            classifications["CUSTOM"] = classify_window(custom_features)

    alignment = compute_alignment(classifications, primary_window)
    acceleration = compute_acceleration_across_windows(windows).to_dict()

    primary_wf = windows.get(primary_window) or next(iter(windows.values()))
    primary_cls = classifications.get(primary_window)
    divergence = compute_divergence(
        primary_wf, window_returns_pct.get(primary_window)
    ).to_dict()

    short_wf = windows.get("3D", primary_wf)
    persistence = compute_persistence(short_wf, primary_wf).to_dict()

    primary_rows = (
        primary_wf_rows(days, primary_window, market_date=market_date)
        if primary_window in WINDOWS
        else [
            row
            for day in days
            if not period_metadata.get("broker_session_dates")
            or str(day.market_date)[:10]
            in {str(value)[:10] for value in period_metadata.get("broker_session_dates", []) or []}
            for row in day.rows
        ]
    )
    foreign_net = sum(
        (r.get("net_value") or 0.0)
        for r in primary_rows
        if str(r.get("broker_type", "")).upper() == "ASING"
    )
    foreign_protection = check_foreign_double_count(
        broker_flow_foreign_net=float(foreign_net),
        aggregate_foreign_net=aggregate_foreign_net,
        flow_origin="DERIVED_FROM_BROKER",
    ).to_dict()

    score = primary_cls.score if primary_cls else 0.0
    confidence = primary_cls.confidence if primary_cls else 0.0
    penalty = primary_cls.penalty if primary_cls else 0.0
    blocker = any(c.blocker for c in classifications.values())
    context_label = primary_cls.classification if primary_cls else "INSUFFICIENT_DATA"

    period_alignment = "INSUFFICIENT"
    if period_type not in {"", "1D", "1DAY", "DAY"}:
        period_alignment = primary_pulse_alignment(
            primary_wf.cumulative_net_value if primary_wf else None,
            today_pulse.get("today_pulse_net_flow"),
            pulse_status=str(today_pulse.get("today_pulse_status", "NOT_AVAILABLE")),
        )

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
        period_metadata=period_metadata,
        today_pulse=today_pulse,
        broker_period_alignment=period_alignment,
    )


def primary_wf_rows(
    days: list[BrokerDay],
    primary_window: str,
    *,
    market_date: str | None = None,
) -> list[dict[str, Any]]:
    selected, _expected_dates = select_window_days(
        days,
        primary_window,
        as_of_date=market_date,
    )
    return [r for d in selected for r in d.rows]
