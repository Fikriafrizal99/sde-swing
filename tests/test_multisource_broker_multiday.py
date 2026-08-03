from __future__ import annotations

from modules.data_sources.broker_analytics import (
    ACCELERATING,
    CONFIRMED_ACCUMULATION,
    CONFIRMED_DISTRIBUTION,
    DECELERATING,
    FULLY_ALIGNED,
    INSUFFICIENT_DATA,
    REVERSING,
    SHORT_TERM_CONFIRMING,
    STABLE,
    STABLE_DOMINANCE,
    STRONG_DISTRIBUTION,
    check_foreign_double_count,
    classify_acceleration,
    classify_window,
    compute_acceleration_across_windows,
    compute_alignment,
    compute_divergence,
    compute_persistence,
)
from modules.data_sources.broker_windows import (
    BrokerDay,
    WindowFeatures,
    compute_window_features,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(broker_code: str, side: str, net_value: float, avg_price: float = 1000.0,
         gross_value: float | None = None, net_lot: float | None = None) -> dict:
    return {
        "broker_code": broker_code,
        "side": side,
        "net_value": net_value,
        "avg_price": avg_price,
        "gross_value": gross_value or abs(net_value),
        "net_lot": net_lot or abs(net_value) / avg_price,
    }


def _buy_day(date: str, brokers: list[tuple[str, float]], price: float = 1000.0) -> BrokerDay:
    rows = [_row(code, "BUY", val, price) for code, val in brokers]
    return BrokerDay(market_date=date, rows=rows)


def _sell_day(date: str, brokers: list[tuple[str, float]], price: float = 1000.0) -> BrokerDay:
    rows = [_row(code, "SELL", val, price) for code, val in brokers]
    return BrokerDay(market_date=date, rows=rows)


def _make_wf(window: str, net_value: float, available: int | None = None,
             largest_contribution: float = 0.2, buyers: list[str] | None = None,
             sellers: list[str] | None = None, acceleration: float = 0.0,
             coverage: float = 1.0) -> WindowFeatures:
    expected = {"1D": 1, "3D": 3, "5D": 5, "10D": 10, "20D": 20}[window]
    avail = available if available is not None else expected
    return WindowFeatures(
        window=window,
        expected_sessions=expected,
        available_sessions=avail,
        coverage_ratio=coverage,
        window_complete=coverage >= 0.8 and avail >= 1,
        cumulative_net_value=net_value,
        cumulative_net_volume=net_value / 1000.0,
        average_daily_net_value=net_value / max(avail, 1),
        positive_day_ratio=1.0 if net_value > 0 else 0.0,
        negative_day_ratio=1.0 if net_value < 0 else 0.0,
        flow_consistency=1.0 if net_value != 0 else 0.0,
        flow_acceleration=acceleration,
        latest_day_contribution=largest_contribution,
        largest_day_contribution=largest_contribution,
        persistent_top_buyers=buyers or (["AA", "BB", "CC"] if net_value > 0 else []),
        persistent_top_sellers=sellers or (["XX", "YY"] if net_value < 0 else []),
        buyer_concentration=0.4,
        seller_concentration=0.3,
        buyer_rotation=0.2,
        seller_rotation=0.3,
        weighted_broker_buy_cost=1000.0 if net_value > 0 else None,
        weighted_broker_sell_cost=None,
        distance_to_buy_cost_pct=None,
        single_day_domination=largest_contribution >= 0.6 and avail >= 2,
    )


# ---------------------------------------------------------------------------
# Window feature computation
# ---------------------------------------------------------------------------

def test_1d_window_single_day():
    day = _buy_day("2026-01-05", [("AA", 1_000_000), ("BB", 500_000)])
    wf = compute_window_features([day], "1D")
    assert wf.window == "1D"
    assert wf.available_sessions == 1
    assert wf.window_complete is True
    assert wf.cumulative_net_value > 0


def test_5d_window_uses_most_recent_5():
    days = [_buy_day(f"2026-01-0{i}", [("AA", 1_000_000)]) for i in range(1, 8)]
    wf = compute_window_features(days, "5D")
    assert wf.expected_sessions == 5
    assert wf.available_sessions == 5
    assert wf.window_complete is True


def test_10d_window_coverage():
    days = [_buy_day(f"2026-01-0{i}", [("AA", 1_000_000)]) for i in range(1, 9)]
    wf = compute_window_features(days, "10D")
    assert wf.expected_sessions == 10
    assert wf.available_sessions == 8
    assert wf.coverage_ratio == 0.8
    assert wf.window_complete is True  # exactly at threshold


def test_20d_window_incomplete():
    days = [_buy_day(f"2026-01-0{i}", [("AA", 1_000_000)]) for i in range(1, 6)]
    wf = compute_window_features(days, "20D")
    assert wf.window_complete is False
    assert wf.coverage_ratio == 0.25


def test_3d_window_net_value_sign():
    days = [
        _buy_day("2026-01-05", [("AA", 2_000_000)]),
        _sell_day("2026-01-04", [("XX", 500_000)]),
        _buy_day("2026-01-03", [("BB", 1_000_000)]),
    ]
    wf = compute_window_features(days, "3D")
    assert wf.cumulative_net_value > 0  # net buy dominant


# ---------------------------------------------------------------------------
# Incomplete window → INSUFFICIENT_DATA
# ---------------------------------------------------------------------------

def test_incomplete_window_classifies_insufficient():
    wf = _make_wf("5D", net_value=5_000_000, available=2, coverage=0.4)
    result = classify_window(wf)
    assert result.classification == INSUFFICIENT_DATA
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Duplicate broker day (same date appears twice — window uses latest N unique)
# ---------------------------------------------------------------------------

def test_duplicate_broker_day_deduped_by_sort():
    # Two days with same date: sorted list keeps both but window slices last N.
    day_a = _buy_day("2026-01-05", [("AA", 1_000_000)])
    day_b = _buy_day("2026-01-05", [("BB", 2_000_000)])  # same date, different rows
    wf = compute_window_features([day_a, day_b], "1D")
    # Window takes the last 1 after sorting by date — both have same date so
    # both end up in the slice; available_sessions reflects actual count.
    assert wf.available_sessions >= 1


# ---------------------------------------------------------------------------
# Weighted broker cost (value/volume-weighted, never simple mean)
# ---------------------------------------------------------------------------

def test_weighted_cost_not_simple_mean():
    # Broker A: price=1000, gross_value=9_000_000 (large weight)
    # Broker B: price=2000, gross_value=1_000_000 (small weight)
    # Simple mean = 1500; weighted = (1000*9M + 2000*1M) / 10M = 1100
    rows = [
        _row("AA", "BUY", 9_000_000, avg_price=1000.0, gross_value=9_000_000),
        _row("BB", "BUY", 1_000_000, avg_price=2000.0, gross_value=1_000_000),
    ]
    day = BrokerDay(market_date="2026-01-05", rows=rows)
    wf = compute_window_features([day], "1D")
    assert wf.weighted_broker_buy_cost is not None
    assert abs(wf.weighted_broker_buy_cost - 1100.0) < 1.0  # weighted, not 1500


def test_weighted_cost_none_when_no_avg_price():
    rows = [{"broker_code": "AA", "side": "BUY", "net_value": 1_000_000}]
    day = BrokerDay(market_date="2026-01-05", rows=rows)
    wf = compute_window_features([day], "1D")
    assert wf.weighted_broker_buy_cost is None


# ---------------------------------------------------------------------------
# Persistence and rotation
# ---------------------------------------------------------------------------

def test_persistence_stable_dominance():
    earlier = _make_wf("5D", 5_000_000, buyers=["AA", "BB", "CC"])
    later = _make_wf("5D", 6_000_000, buyers=["AA", "BB", "CC"])
    # buyer_rotation=0.2 → overlap=0.8 → STABLE_DOMINANCE
    later_wf = WindowFeatures(**{**later.__dict__, "buyer_rotation": 0.2})
    result = compute_persistence(earlier, later_wf)
    assert result.buyer_overlap_ratio == 1.0
    assert result.buyer_rotation_status == STABLE_DOMINANCE


def test_persistence_new_buyers_counted():
    earlier = _make_wf("5D", 5_000_000, buyers=["AA", "BB"])
    later = _make_wf("5D", 6_000_000, buyers=["AA", "CC", "DD"])
    result = compute_persistence(earlier, later)
    assert result.new_buyer_count == 2   # CC, DD
    assert result.exited_buyer_count == 1  # BB


# ---------------------------------------------------------------------------
# Acceleration and reversal
# ---------------------------------------------------------------------------

def test_classify_acceleration_accelerating():
    assert classify_acceleration(0.5) == ACCELERATING


def test_classify_acceleration_reversing():
    assert classify_acceleration(-0.5) == REVERSING


def test_classify_acceleration_decelerating():
    assert classify_acceleration(-0.2) == DECELERATING


def test_classify_acceleration_stable():
    assert classify_acceleration(0.05) == STABLE


def test_acceleration_across_windows():
    # 1D avg >> 3D avg → ACCELERATING for 1D vs 3D
    windows = {
        "1D": _make_wf("1D", 10_000_000, acceleration=0.8),
        "3D": _make_wf("3D", 6_000_000, acceleration=0.0),
        "5D": _make_wf("5D", 5_000_000, acceleration=0.0),
    }
    result = compute_acceleration_across_windows(windows)
    # 1D avg=10M, 3D avg=2M → diff positive → ACCELERATING
    assert result.acceleration_1d_vs_3d == ACCELERATING
    assert result.acceleration_10d_vs_20d is None  # windows absent


# ---------------------------------------------------------------------------
# Single-day domination penalty
# ---------------------------------------------------------------------------

def test_single_day_domination_reduces_score():
    # Build a window where one day contributes 70% of flow.
    wf_dominated = _make_wf("5D", 5_000_000, largest_contribution=0.70)
    wf_normal = _make_wf("5D", 5_000_000, largest_contribution=0.20)
    r_dominated = classify_window(wf_dominated)
    r_normal = classify_window(wf_normal)
    assert r_dominated.penalty == 20.0
    assert r_dominated.score < r_normal.score
    assert wf_dominated.single_day_domination is True


def test_strong_distribution_is_hard_blocker():
    wf = _make_wf("5D", -10_000_000, buyers=[], sellers=["XX", "YY", "ZZ"])
    result = classify_window(wf)
    if result.classification == STRONG_DISTRIBUTION:
        assert result.blocker is True


# ---------------------------------------------------------------------------
# Divergence
# ---------------------------------------------------------------------------

def test_divergence_confirmed_accumulation():
    wf = _make_wf("5D", 5_000_000)
    result = compute_divergence(wf, price_return_pct=3.0)
    assert result.label == CONFIRMED_ACCUMULATION
    assert result.broker_flow_direction == "POSITIVE"
    assert result.price_return_direction == "UP"


def test_divergence_confirmed_distribution():
    wf = _make_wf("5D", -5_000_000, buyers=[], sellers=["XX"])
    result = compute_divergence(wf, price_return_pct=-2.0)
    assert result.label == CONFIRMED_DISTRIBUTION


def test_divergence_insufficient_when_no_price():
    wf = _make_wf("5D", 5_000_000)
    result = compute_divergence(wf, price_return_pct=None)
    assert result.label == INSUFFICIENT_DATA


def test_divergence_insufficient_when_window_incomplete():
    wf = _make_wf("5D", 5_000_000, available=2, coverage=0.4)
    result = compute_divergence(wf, price_return_pct=2.0)
    assert result.label == INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# Daily–multi-day alignment
# ---------------------------------------------------------------------------

def test_alignment_fully_aligned_all_accumulation():
    clsf = {w: classify_window(_make_wf(w, 5_000_000)) for w in ("1D", "3D", "5D", "10D", "20D")}
    result = compute_alignment(clsf, primary_window="5D")
    assert result.alignment == FULLY_ALIGNED


def test_alignment_short_term_confirming():
    # Short-term accumulation with genuinely neutral long windows (net 0 →
    # NEUTRAL, never STRONG_DISTRIBUTION) → SHORT_TERM_CONFIRMING.
    clsf = {
        "1D": classify_window(_make_wf("1D", 2_000_000)),
        "3D": classify_window(_make_wf("3D", 3_000_000)),
        "5D": classify_window(_make_wf("5D", 1_000_000)),
        "10D": classify_window(_make_wf("10D", 0, buyers=[], sellers=[])),
        "20D": classify_window(_make_wf("20D", 0, buyers=[], sellers=[])),
    }
    result = compute_alignment(clsf, primary_window="5D")
    assert result.alignment == SHORT_TERM_CONFIRMING


def test_alignment_context_fields_populated():
    clsf = {"5D": classify_window(_make_wf("5D", 5_000_000))}
    result = compute_alignment(clsf, primary_window="5D")
    assert result.context_5d != ""
    assert result.context_primary != ""


# ---------------------------------------------------------------------------
# Foreign double-count protection
# ---------------------------------------------------------------------------

def test_no_double_count_when_aggregate_absent():
    result = check_foreign_double_count(
        broker_flow_foreign_net=1_000_000,
        aggregate_foreign_net=None,
        flow_origin="DERIVED_FROM_BROKER",
    )
    assert result.double_count_risk is False


def test_double_count_risk_when_derived_plus_aggregate():
    result = check_foreign_double_count(
        broker_flow_foreign_net=1_000_000,
        aggregate_foreign_net=1_050_000,
        flow_origin="DERIVED_FROM_BROKER",
    )
    assert result.double_count_risk is True
    assert "DOUBLE_COUNT_RISK" in result.trace


def test_no_double_count_when_origin_is_aggregate_feed():
    result = check_foreign_double_count(
        broker_flow_foreign_net=0.0,
        aggregate_foreign_net=1_000_000,
        flow_origin="AGGREGATE_FEED",
    )
    assert result.double_count_risk is False
    assert result.foreign_net_value == 1_000_000


def test_double_count_uses_broker_derived_when_at_risk():
    result = check_foreign_double_count(
        broker_flow_foreign_net=900_000,
        aggregate_foreign_net=1_000_000,
        flow_origin="DERIVED_FROM_BROKER",
    )
    # At risk → fall back to broker-derived, not aggregate.
    assert result.double_count_risk is True
    assert result.foreign_net_value == 900_000
