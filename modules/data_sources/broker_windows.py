from __future__ import annotations

"""Broker multi-day window definitions and per-window feature computation.

Windows use the IDX trading calendar, not calendar days.  Each window computes
cumulative flow, consistency, acceleration, weighted broker cost, persistence,
concentration, and coverage.  Nothing here makes a BUY/WATCH/AVOID decision.

Broker cost is weighted by traded value/volume (not a simple average), and a
window is never reported complete when coverage is short.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable

# Window sizes in trading sessions.
WINDOWS: dict[str, int] = {"1D": 1, "3D": 3, "5D": 5, "10D": 10, "20D": 20}
DEFAULT_PRIMARY_WINDOW = "5D"

# A window is "complete" only at/above this coverage ratio.
COVERAGE_COMPLETE_THRESHOLD = 0.8


@dataclass
class BrokerDay:
    """One session's broker rows for a single symbol."""

    market_date: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def net_value(self) -> float:
        # Net across brokers: BUY net positive, SELL net negative.
        total = 0.0
        for r in self.rows:
            side = str(r.get("side", "")).upper()
            nv = _f(r.get("net_value")) or 0.0
            total += nv if side == "BUY" else -abs(nv)
        return total

    def net_volume(self) -> float:
        total = 0.0
        for r in self.rows:
            side = str(r.get("side", "")).upper()
            nl = _f(r.get("net_lot")) or 0.0
            total += nl if side == "BUY" else -abs(nl)
        return total

    def top_buyers(self, limit: int = 5) -> list[str]:
        buys = [r for r in self.rows if str(r.get("side", "")).upper() == "BUY"]
        buys.sort(key=lambda r: abs(_f(r.get("net_value")) or 0.0), reverse=True)
        return [str(r.get("broker_code", "")) for r in buys[:limit] if r.get("broker_code")]

    def top_sellers(self, limit: int = 5) -> list[str]:
        sells = [r for r in self.rows if str(r.get("side", "")).upper() == "SELL"]
        sells.sort(key=lambda r: abs(_f(r.get("net_value")) or 0.0), reverse=True)
        return [str(r.get("broker_code", "")) for r in sells[:limit] if r.get("broker_code")]


@dataclass
class WindowFeatures:
    window: str
    expected_sessions: int
    available_sessions: int
    coverage_ratio: float
    window_complete: bool
    cumulative_net_value: float
    cumulative_net_volume: float
    average_daily_net_value: float
    positive_day_ratio: float
    negative_day_ratio: float
    flow_consistency: float
    flow_acceleration: float
    latest_day_contribution: float
    largest_day_contribution: float
    persistent_top_buyers: list[str]
    persistent_top_sellers: list[str]
    buyer_concentration: float
    seller_concentration: float
    buyer_rotation: float
    seller_rotation: float
    weighted_broker_buy_cost: float | None
    weighted_broker_sell_cost: float | None
    distance_to_buy_cost_pct: float | None
    single_day_domination: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "expected_sessions": self.expected_sessions,
            "available_sessions": self.available_sessions,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "window_complete": self.window_complete,
            "cumulative_net_value": self.cumulative_net_value,
            "cumulative_net_volume": self.cumulative_net_volume,
            "average_daily_net_value": self.average_daily_net_value,
            "positive_day_ratio": round(self.positive_day_ratio, 4),
            "negative_day_ratio": round(self.negative_day_ratio, 4),
            "flow_consistency": round(self.flow_consistency, 4),
            "flow_acceleration": round(self.flow_acceleration, 4),
            "latest_day_contribution": round(self.latest_day_contribution, 4),
            "largest_day_contribution": round(self.largest_day_contribution, 4),
            "persistent_top_buyers": self.persistent_top_buyers,
            "persistent_top_sellers": self.persistent_top_sellers,
            "buyer_concentration": round(self.buyer_concentration, 4),
            "seller_concentration": round(self.seller_concentration, 4),
            "buyer_rotation": round(self.buyer_rotation, 4),
            "seller_rotation": round(self.seller_rotation, 4),
            "weighted_broker_buy_cost": self.weighted_broker_buy_cost,
            "weighted_broker_sell_cost": self.weighted_broker_sell_cost,
            "distance_to_buy_cost_pct": self.distance_to_buy_cost_pct,
            "single_day_domination": self.single_day_domination,
        }


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _weighted_cost(rows: list[dict[str, Any]], side: str) -> float | None:
    """Value/volume-weighted average broker cost for one side.

    Weighted by gross value when avg_price is present; falls back to
    net_lot-weighted.  Never a simple mean of prices.
    """
    num = 0.0
    den = 0.0
    for r in rows:
        if str(r.get("side", "")).upper() != side:
            continue
        price = _f(r.get("avg_price"))
        if price is None or price <= 0:
            continue
        weight = _f(r.get("gross_value"))
        if weight is None or weight <= 0:
            lot = _f(r.get("gross_lot")) or _f(r.get("net_lot"))
            weight = abs(lot) * price if lot else 0.0
        if weight <= 0:
            continue
        num += price * weight
        den += weight
    if den <= 0:
        return None
    return round(num / den, 4)


def _concentration(rows: list[dict[str, Any]], side: str) -> float:
    """Herfindahl-style concentration of net value among brokers on one side."""
    vals = [
        abs(_f(r.get("net_value")) or 0.0)
        for r in rows
        if str(r.get("side", "")).upper() == side
    ]
    total = sum(vals)
    if total <= 0:
        return 0.0
    return round(sum((v / total) ** 2 for v in vals), 4)


def _persistent_brokers(days: list[BrokerDay], side: str, limit: int = 5) -> tuple[list[str], float]:
    """Brokers appearing in the top ranks across most sessions + overlap ratio."""
    if not days:
        return [], 0.0
    per_day_sets: list[set[str]] = []
    counts: dict[str, int] = {}
    for day in days:
        top = day.top_buyers(limit) if side == "BUY" else day.top_sellers(limit)
        per_day_sets.append(set(top))
        for code in top:
            counts[code] = counts.get(code, 0) + 1
    # Persistent = present in >= half of the sessions.
    threshold = max(1, len(days) // 2)
    persistent = sorted(
        [code for code, n in counts.items() if n >= threshold],
        key=lambda c: counts[c],
        reverse=True,
    )[:limit]
    # Overlap ratio: average pairwise Jaccard of consecutive sessions.
    if len(per_day_sets) < 2:
        overlap = 1.0 if per_day_sets and per_day_sets[0] else 0.0
    else:
        ratios = []
        for a, b in zip(per_day_sets, per_day_sets[1:]):
            union = a | b
            ratios.append(len(a & b) / len(union) if union else 0.0)
        overlap = sum(ratios) / len(ratios) if ratios else 0.0
    return persistent, round(overlap, 4)


def compute_window_features(
    days: list[BrokerDay],
    window: str,
    *,
    current_price: float | None = None,
) -> WindowFeatures:
    expected = WINDOWS[window]
    # Use the most recent `expected` sessions available.
    ordered = sorted(days, key=lambda d: d.market_date)[-expected:]
    available = len(ordered)
    coverage = available / expected if expected else 0.0
    complete = coverage >= COVERAGE_COMPLETE_THRESHOLD and available >= 1

    net_values = [d.net_value() for d in ordered]
    net_volumes = [d.net_volume() for d in ordered]
    cumulative_value = sum(net_values)
    cumulative_volume = sum(net_volumes)
    avg_daily = cumulative_value / available if available else 0.0

    positive_days = sum(1 for v in net_values if v > 0)
    negative_days = sum(1 for v in net_values if v < 0)
    positive_ratio = positive_days / available if available else 0.0
    negative_ratio = negative_days / available if available else 0.0

    # Flow consistency: fraction of days sharing the dominant sign.
    if net_values:
        dominant = positive_days if cumulative_value >= 0 else negative_days
        consistency = dominant / available
    else:
        consistency = 0.0

    # Flow acceleration: latest-half average vs earlier-half average.
    acceleration = _acceleration(net_values)

    # Contributions.
    abs_total = sum(abs(v) for v in net_values)
    latest_contribution = (abs(net_values[-1]) / abs_total) if abs_total else 0.0
    largest_contribution = (max((abs(v) for v in net_values), default=0.0) / abs_total) if abs_total else 0.0
    # Single-day domination: one session drives most of the window flow.
    single_day_domination = largest_contribution >= 0.6 and available >= 2

    all_rows: list[dict[str, Any]] = [r for d in ordered for r in d.rows]
    buy_cost = _weighted_cost(all_rows, "BUY")
    sell_cost = _weighted_cost(all_rows, "SELL")
    distance = None
    if current_price is not None and buy_cost:
        distance = round(100.0 * (current_price - buy_cost) / buy_cost, 4)

    persistent_buyers, buyer_overlap = _persistent_brokers(ordered, "BUY")
    persistent_sellers, seller_overlap = _persistent_brokers(ordered, "SELL")

    return WindowFeatures(
        window=window,
        expected_sessions=expected,
        available_sessions=available,
        coverage_ratio=coverage,
        window_complete=complete,
        cumulative_net_value=cumulative_value,
        cumulative_net_volume=cumulative_volume,
        average_daily_net_value=avg_daily,
        positive_day_ratio=positive_ratio,
        negative_day_ratio=negative_ratio,
        flow_consistency=consistency,
        flow_acceleration=acceleration,
        latest_day_contribution=latest_contribution,
        largest_day_contribution=largest_contribution,
        persistent_top_buyers=persistent_buyers,
        persistent_top_sellers=persistent_sellers,
        buyer_concentration=_concentration(all_rows, "BUY"),
        seller_concentration=_concentration(all_rows, "SELL"),
        buyer_rotation=round(1.0 - buyer_overlap, 4),
        seller_rotation=round(1.0 - seller_overlap, 4),
        weighted_broker_buy_cost=buy_cost,
        weighted_broker_sell_cost=sell_cost,
        distance_to_buy_cost_pct=distance,
        single_day_domination=single_day_domination,
    )


def _acceleration(net_values: list[float]) -> float:
    """Positive when recent flow exceeds earlier flow, normalized to [-1, 1]."""
    n = len(net_values)
    if n < 2:
        return 0.0
    half = n // 2
    earlier = net_values[:half] or net_values[:1]
    recent = net_values[half:]
    earlier_avg = sum(earlier) / len(earlier)
    recent_avg = sum(recent) / len(recent)
    scale = max(abs(earlier_avg), abs(recent_avg), 1.0)
    return round(max(-1.0, min(1.0, (recent_avg - earlier_avg) / scale)), 4)