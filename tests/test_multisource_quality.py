from __future__ import annotations

from datetime import datetime, timedelta

from modules.data_sources import constants as C
from modules.data_sources.canonical import (
    DailyBar,
    OrderBookSnapshot,
    TradingStatus,
    now_wib,
)
from modules.data_sources.config import SourceConfig
from modules.data_sources.data_quality import DataQualityEngine

# A fixed trading Monday for deterministic date checks.
FIXED_NOW = datetime(2026, 1, 5, 17, 0, tzinfo=C.WIB)  # Monday after close


def _engine():
    return DataQualityEngine(holidays=[], min_microstructure_metrics=2)


def _bar(**kwargs):
    base = dict(
        symbol="BBCA",
        market_date="2026-01-05",
        event_timestamp=FIXED_NOW.isoformat(),
        received_at=FIXED_NOW.isoformat(),
        source="ZAPI_IDX",
        open=1000.0, high=1050.0, low=990.0, close=1030.0, volume=5_000_000.0,
    )
    base.update(kwargs)
    return DailyBar(**base)


def test_valid_bar_accepted():
    result = _engine().validate(_bar(), at=FIXED_NOW)
    assert result.accepted
    assert result.status == C.QUALITY_OK


def test_future_dated_data_rejected():
    bar = _bar(market_date="2026-01-06")
    result = _engine().validate(bar, at=FIXED_NOW)
    assert not result.accepted
    assert C.FUTURE_DATED_DATA in result.reasons


def test_missing_required_field_rejected():
    bar = _bar(close=None)
    result = _engine().validate(bar, at=FIXED_NOW)
    assert not result.accepted
    assert C.MISSING_REQUIRED_FIELD in result.reasons


def test_invalid_ohlc_rejected():
    bar = _bar(high=900.0)  # high < low/open/close
    result = _engine().validate(bar, at=FIXED_NOW)
    assert not result.accepted
    assert C.INVALID_OHLC in result.reasons


def test_negative_volume_rejected():
    bar = _bar(volume=-1.0)
    result = _engine().validate(bar, at=FIXED_NOW)
    assert not result.accepted
    assert C.NEGATIVE_VOLUME in result.reasons


def test_non_trading_day_rejected():
    # 2026-01-04 is a Sunday.
    bar = _bar(market_date="2026-01-04")
    result = _engine().validate(bar, at=FIXED_NOW)
    assert not result.accepted
    assert C.NON_TRADING_DAY in result.reasons


def test_wrong_market_date_flagged():
    bar = _bar()
    result = _engine().validate(bar, expected_market_date="2026-01-02", at=FIXED_NOW)
    assert not result.accepted
    assert C.WRONG_MARKET_DATE in result.reasons


def test_stale_data_warns():
    src = SourceConfig(name="ZAPI_IDX", maximum_stale_seconds=60)
    old = FIXED_NOW - timedelta(hours=2)
    bar = _bar(event_timestamp=old.isoformat())
    result = _engine().validate(bar, source_config=src, at=FIXED_NOW)
    assert C.STALE_DATA in result.reasons


def test_duplicate_record_detected():
    engine = _engine()
    seen: set = set()
    b1 = _bar()
    b2 = _bar()
    engine.validate(b1, seen_keys=seen, at=FIXED_NOW)
    result = engine.validate(b2, seen_keys=seen, at=FIXED_NOW)
    assert C.DUPLICATE_RECORD in result.reasons


def test_timezone_mismatch_flagged():
    naive = "2026-01-05T17:00:00"  # no tz
    bar = _bar(event_timestamp=naive, received_at=naive)
    result = _engine().validate(bar, at=FIXED_NOW)
    assert C.TIMEZONE_MISMATCH in result.reasons


def test_partial_daily_candle_never_closed():
    bar = _bar(is_closed=False)
    result = _engine().validate(bar, at=FIXED_NOW)
    assert C.PARTIAL_DAILY_CANDLE in result.reasons


def test_suspend_ambiguity_fails_closed():
    status = TradingStatus(
        symbol="BBCA",
        market_date="2026-01-05",
        event_timestamp=FIXED_NOW.isoformat(),
        received_at=FIXED_NOW.isoformat(),
        source="ZAPI_IDX",
        status="SUSPEND",
        ambiguous=True,
    )
    result = _engine().validate(status, at=FIXED_NOW)
    assert not result.accepted
    assert C.SUSPEND_STATUS_AMBIGUOUS in result.reasons


def test_insufficient_microstructure_not_normal():
    book = OrderBookSnapshot(
        symbol="BBCA",
        market_date="2026-01-05",
        event_timestamp=FIXED_NOW.isoformat(),
        received_at=FIXED_NOW.isoformat(),
        source="ZAPI_IDX",
        best_bid=None,
        best_ask=None,
        depth_levels=0,
    )
    result = _engine().validate(book, at=FIXED_NOW)
    assert C.INSUFFICIENT_MICROSTRUCTURE_DATA in result.reasons