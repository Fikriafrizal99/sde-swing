from __future__ import annotations

from datetime import datetime

from modules.data_sources import constants as C
from modules.data_sources.canonical import DailyBar, TradingStatus
from modules.data_sources.config import (
    DataSourceConfig,
    RecordOwnership,
    SourceConfig,
)
from modules.data_sources.conflict_resolver import ConflictResolver
from modules.data_sources.data_quality import DataQualityEngine
from modules.data_sources.router import SourceRouter

FIXED_NOW = datetime(2026, 1, 5, 17, 0, tzinfo=C.WIB)  # Monday after close


def _config(mode: str = C.MODE_PRIMARY_WITH_FALLBACK) -> DataSourceConfig:
    sources = {
        "ZAPI_IDX": SourceConfig(name="ZAPI_IDX", enabled=True, priority=10),
        "HISTORICAL_PROVIDER": SourceConfig(name="HISTORICAL_PROVIDER", enabled=True, priority=30),
    }
    ownership = {
        "DailyBar": RecordOwnership(
            record_type="DailyBar", primary="ZAPI_IDX", fallback=("HISTORICAL_PROVIDER",)
        ),
        "TradingStatus": RecordOwnership(
            record_type="TradingStatus", primary="ZAPI_IDX", fallback=("HISTORICAL_PROVIDER",)
        ),
    }
    return DataSourceConfig(resolver_mode=mode, sources=sources, ownership=ownership)


def _router(mode: str = C.MODE_PRIMARY_WITH_FALLBACK) -> SourceRouter:
    cfg = _config(mode)
    quality = DataQualityEngine(holidays=[], min_microstructure_metrics=2)
    resolver = ConflictResolver(
        mode=mode,
        source_priorities={"ZAPI_IDX": 10, "HISTORICAL_PROVIDER": 30},
        numeric_tolerance_pct=0.005,
    )
    return SourceRouter(cfg, quality, resolver)


def _bar(source, close=1030.0, **kwargs):
    base = dict(
        symbol="BBCA", market_date="2026-01-05",
        event_timestamp=FIXED_NOW.isoformat(), received_at=FIXED_NOW.isoformat(),
        source=source, open=1000.0, high=1050.0, low=990.0, close=close,
        volume=5_000_000.0,
    )
    base.update(kwargs)
    return DailyBar(**base)


def test_primary_selected_when_valid():
    router = _router()
    candidates = {"ZAPI_IDX": _bar("ZAPI_IDX"), "HISTORICAL_PROVIDER": _bar("HISTORICAL_PROVIDER")}
    result = router.route("DailyBar", "BBCA", "2026-01-05", candidates, at=FIXED_NOW)
    assert result.source_used == "ZAPI_IDX"
    assert result.fallback_used is False
    assert result.record is not None


def test_fallback_used_when_primary_missing():
    router = _router()
    candidates = {"HISTORICAL_PROVIDER": _bar("HISTORICAL_PROVIDER")}
    result = router.route("DailyBar", "BBCA", "2026-01-05", candidates, at=FIXED_NOW)
    assert result.source_used == "HISTORICAL_PROVIDER"
    assert result.fallback_used is True


def test_fallback_used_when_primary_invalid():
    router = _router()
    bad_primary = _bar("ZAPI_IDX", high=100.0)  # invalid OHLC → rejected
    good_fallback = _bar("HISTORICAL_PROVIDER")
    candidates = {"ZAPI_IDX": bad_primary, "HISTORICAL_PROVIDER": good_fallback}
    result = router.route("DailyBar", "BBCA", "2026-01-05", candidates, at=FIXED_NOW)
    assert result.source_used == "HISTORICAL_PROVIDER"
    assert result.fallback_used is True


def test_primary_only_does_not_fall_back():
    router = _router(mode=C.MODE_PRIMARY_ONLY)
    bad_primary = _bar("ZAPI_IDX", high=100.0)  # invalid → rejected
    good_fallback = _bar("HISTORICAL_PROVIDER")
    candidates = {"ZAPI_IDX": bad_primary, "HISTORICAL_PROVIDER": good_fallback}
    result = router.route("DailyBar", "BBCA", "2026-01-05", candidates, at=FIXED_NOW)
    # PRIMARY_ONLY: a rejected primary means no data, never a fallback.
    assert result.record is None
    assert result.source_used == ""


def test_no_valid_candidates_returns_none():
    router = _router()
    result = router.route("DailyBar", "BBCA", "2026-01-05", {}, at=FIXED_NOW)
    assert result.record is None
    assert result.source_used == ""
    assert result.fallback_used is False


def test_trading_status_ambiguity_fails_closed():
    router = _router()
    a = TradingStatus(
        symbol="BBCA", market_date="2026-01-05",
        event_timestamp=FIXED_NOW.isoformat(), received_at=FIXED_NOW.isoformat(),
        source="ZAPI_IDX", status="NORMAL", is_suspended=False,
    )
    b = TradingStatus(
        symbol="BBCA", market_date="2026-01-05",
        event_timestamp=FIXED_NOW.isoformat(), received_at=FIXED_NOW.isoformat(),
        source="HISTORICAL_PROVIDER", status="SUSPEND", is_suspended=True,
    )
    candidates = {"ZAPI_IDX": a, "HISTORICAL_PROVIDER": b}
    result = router.route("TradingStatus", "BBCA", "2026-01-05", candidates, at=FIXED_NOW)
    assert result.resolution is not None
    assert result.resolution.fail_closed is True
    assert result.record.is_tradable is False
    assert result.record.quality_status == C.QUALITY_REJECTED


def test_router_never_leaks_source_into_domain():
    # The winner carries provenance, but downstream sees only a canonical record.
    router = _router()
    candidates = {"ZAPI_IDX": _bar("ZAPI_IDX")}
    result = router.route("DailyBar", "BBCA", "2026-01-05", candidates, at=FIXED_NOW)
    assert result.record.record_type == "DailyBar"
    # Domain fields carry no source name.
    assert "ZAPI" not in str(result.record.close)
