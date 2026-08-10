from __future__ import annotations

"""Broker multi-day window definitions and per-window feature computation.

Production windows are anchored to an explicit IDX market session.  A missing
session therefore stays missing instead of being replaced by an older file.
Legacy/unit callers that omit ``as_of_date`` retain the historical "most recent
N observations" behaviour for backward compatibility.

Nothing here makes a BUY/WATCH/AVOID decision.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.market_calendar.idx_calendar import is_idx_trading_day, previous_idx_trading_day

WINDOWS: dict[str, int] = {"1D": 1, "3D": 3, "5D": 5, "10D": 10, "20D": 20}
DEFAULT_PRIMARY_WINDOW = "5D"
COVERAGE_COMPLETE_THRESHOLD = 0.8
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRADING_CALENDAR_PATH = PROJECT_ROOT / "config/trading_calendar.json"


@dataclass
class BrokerDay:
    """One session's broker rows for a single symbol."""

    market_date: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def net_value(self) -> float:
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
    return f if f == f else None


def _calendar_rules() -> tuple[list[str], list[str]]:
    if not TRADING_CALENDAR_PATH.exists():
        return [], []
    try:
        payload = json.loads(TRADING_CALENDAR_PATH.read_text(encoding="utf-8"))
    except Exception:
        return [], []
    raw_holidays = payload.get("holidays", {})
    if isinstance(raw_holidays, dict):
        holidays = [
            str(day)
            for day, detail in raw_holidays.items()
            if not isinstance(detail, dict) or bool(detail.get("holiday", True))
        ]
    else:
        holidays = [str(item) for item in raw_holidays or []]
    special = [str(item) for item in payload.get("special_trading_days", []) or []]
    return holidays, special


def expected_session_dates(as_of_date: str, session_count: int) -> list[str]:
    """Return the exact IDX sessions in a window ending at ``as_of_date``."""
    if not as_of_date or session_count <= 0:
        return []
    holidays, special = _calendar_rules()
    try:
        from datetime import date
        end = date.fromisoformat(str(as_of_date)[:10])
    except Exception:
        return []
    if not is_idx_trading_day(end, holidays=holidays, special_trading_days=special):
        return []
    sessions = [end]
    probe = end
    while len(sessions) < session_count:
        probe = previous_idx_trading_day(
            probe,
            holidays=holidays,
            special_trading_days=special,
        )
        sessions.append(probe)
    return [item.isoformat() for item in sorted(sessions)]


def _unique_days(days: list[BrokerDay]) -> list[BrokerDay]:
    """Keep one observation per market date; later input wins deterministically."""
    by_date: dict[str, BrokerDay] = {}
    for day in days:
        by_date[str(day.market_date)] = day
    return [by_date[key] for key in sorted(by_date)]


def select_window_days(
    days: list[BrokerDay],
    window: str,
    *,
    as_of_date: str | None = None,
) -> tuple[list[BrokerDay], list[str]]:
    """Select broker observations for one window.

    With explicit ``as_of_date`` (the production path), only exact IDX session
    dates are eligible.  Without it, the previous N-observation behaviour is
    retained for backward-compatible helpers/tests and historical utilities.
    """
    expected = WINDOWS[window]
    unique = _unique_days(days)
    if as_of_date is None:
        ordered = unique[-expected:]
        return ordered, [day.market_date for day in ordered]

    expected_dates = expected_session_dates(str(as_of_date), expected)
    by_date = {day.market_date: day for day in unique}
    selected = [by_date[session] for session in expected_dates if session in by_date]
    return selected, expected_dates


def _weighted_cost(rows: list[dict[str, Any]], side: str) -> float | None:
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
    if not days:
        return [], 0.0
    per_day_sets: list[set[str]] = []
    counts: dict[str, int] = {}
    for day in days:
        top = day.top_buyers(limit) if side == "BUY" else day.top_sellers(limit)
        per_day_sets.append(set(top))
        for code in top:
            counts[code] = counts.get(code, 0) + 1
    threshold = max(1, len(days) // 2)
    persistent = sorted(
        [code for code, n in counts.items() if n >= threshold],
        key=lambda code: counts[code],
        reverse=True,
    )[:limit]
    if len(per_day_sets) < 2:
        overlap = 1.0 if per_day_sets and per_day_sets[0] else 0.0
    else:
        ratios = []
        for left, right in zip(per_day_sets, per_day_sets[1:]):
            union = left | right
            ratios.append(len(left & right) / len(union) if union else 0.0)
        overlap = sum(ratios) / len(ratios) if ratios else 0.0
    return persistent, round(overlap, 4)


def compute_window_features(
    days: list[BrokerDay],
    window: str,
    *,
    current_price: float | None = None,
    as_of_date: str | None = None,
) -> WindowFeatures:
    expected = WINDOWS[window]
    ordered, expected_dates = select_window_days(days, window, as_of_date=as_of_date)
    available = len(ordered)
    coverage = available / expected if expected else 0.0
    if as_of_date is None:
        # Compatibility contract: old callers considered >=80% coverage usable.
        complete = coverage >= COVERAGE_COMPLETE_THRESHOLD and available >= 1
    else:
        # Production contract: named N-session windows are complete only when
        # every expected IDX session exists. Missing != zero flow.
        complete = bool(expected_dates) and available == expected

    net_values = [day.net_value() for day in ordered]
    net_volumes = [day.net_volume() for day in ordered]
    cumulative_value = sum(net_values)
    cumulative_volume = sum(net_volumes)
    avg_daily = cumulative_value / available if available else 0.0

    positive_days = sum(1 for value in net_values if value > 0)
    negative_days = sum(1 for value in net_values if value < 0)
    positive_ratio = positive_days / available if available else 0.0
    negative_ratio = negative_days / available if available else 0.0
    if net_values:
        dominant = positive_days if cumulative_value >= 0 else negative_days
        consistency = dominant / available
    else:
        consistency = 0.0

    acceleration = _acceleration(net_values)
    abs_total = sum(abs(value) for value in net_values)
    latest_contribution = abs(net_values[-1]) / abs_total if abs_total else 0.0
    largest_contribution = max((abs(value) for value in net_values), default=0.0) / abs_total if abs_total else 0.0
    single_day_domination = largest_contribution >= 0.6 and available >= 2

    all_rows: list[dict[str, Any]] = [row for day in ordered for row in day.rows]
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
    if len(net_values) < 2:
        return 0.0
    half = len(net_values) // 2
    earlier = net_values[:half] or net_values[:1]
    recent = net_values[half:]
    earlier_avg = sum(earlier) / len(earlier)
    recent_avg = sum(recent) / len(recent)
    scale = max(abs(earlier_avg), abs(recent_avg), 1.0)
    return round(max(-1.0, min(1.0, (recent_avg - earlier_avg) / scale)), 4)
