from __future__ import annotations

from modules.data_sources import constants as C
from modules.data_sources.canonical import DailyBar, TradingStatus, now_wib
from modules.data_sources.conflict_resolver import ConflictResolver

PRIORITIES = {"ZAPI_IDX": 10, "HISTORICAL_PROVIDER": 30}


def _bar(source, close, **kwargs):
    now = now_wib().isoformat()
    base = dict(
        symbol="BBCA", market_date="2026-01-05", event_timestamp=now,
        received_at=now, source=source, open=1000.0, high=1050.0, low=990.0,
        close=close, volume=1_000_000.0,
    )
    base.update(kwargs)
    return DailyBar(**base)


def test_single_source_no_conflict():
    resolver = ConflictResolver(source_priorities=PRIORITIES)
    result = resolver.resolve([_bar("ZAPI_IDX", 1030.0)])
    assert result.conflict_status == C.CONFLICT_NONE
    assert result.record.close == 1030.0


def test_within_tolerance_keeps_primary():
    resolver = ConflictResolver(source_priorities=PRIORITIES, numeric_tolerance_pct=0.01)
    primary = _bar("ZAPI_IDX", 1030.0)
    fallback = _bar("HISTORICAL_PROVIDER", 1035.0)  # ~0.5% diff
    result = resolver.resolve([primary, fallback])
    assert result.record.source == "ZAPI_IDX"
    assert result.record.close == 1030.0


def test_field_level_resolution_records_candidates():
    resolver = ConflictResolver(source_priorities=PRIORITIES, numeric_tolerance_pct=0.0001)
    primary = _bar("ZAPI_IDX", 1030.0)
    fallback = _bar("HISTORICAL_PROVIDER", 1200.0)  # big diff
    result = resolver.resolve([primary, fallback])
    assert result.conflict_status == C.CONFLICT_RESOLVED
    close_res = [fr for fr in result.field_resolutions if fr.field_name == "close"]
    assert close_res, "close conflict should be recorded"
    fr = close_res[0]
    assert fr.selected_source == "ZAPI_IDX"
    assert len(fr.candidate_values) == 2
    assert fr.difference == 170.0


def test_market_date_mismatch_not_merged():
    resolver = ConflictResolver(source_priorities=PRIORITIES)
    a = _bar("ZAPI_IDX", 1030.0)
    b = _bar("HISTORICAL_PROVIDER", 1030.0)
    b.market_date = "2026-01-02"
    result = resolver.resolve([a, b])
    assert result.conflict_status == C.CONFLICT_UNRESOLVED
    assert "MARKET_DATE_MISMATCH" in result.reason


def test_suspend_conflict_fails_closed():
    resolver = ConflictResolver(source_priorities=PRIORITIES)
    now = now_wib().isoformat()
    a = TradingStatus(symbol="BBCA", market_date="2026-01-05", event_timestamp=now,
                      received_at=now, source="ZAPI_IDX", status="NORMAL", is_suspended=False)
    b = TradingStatus(symbol="BBCA", market_date="2026-01-05", event_timestamp=now,
                      received_at=now, source="HISTORICAL_PROVIDER", status="SUSPEND", is_suspended=True)
    result = resolver.resolve([a, b])
    assert result.fail_closed is True
    assert result.conflict_status == C.CONFLICT_FAIL_CLOSED
    assert result.record.is_tradable is False


def test_corporate_action_explains_ohlc_gap():
    resolver = ConflictResolver(
        source_priorities=PRIORITIES,
        numeric_tolerance_pct=0.0001,
        corporate_action_lookup=lambda symbol, date: True,
    )
    primary = _bar("ZAPI_IDX", 1030.0)
    fallback = _bar("HISTORICAL_PROVIDER", 2060.0)  # looks like a split
    result = resolver.resolve([primary, fallback])
    close_res = [fr for fr in result.field_resolutions if fr.field_name == "close"][0]
    assert close_res.resolution_reason == "CORPORATE_ACTION_EXPLAINS_GAP"
    assert close_res.conflict_severity == C.SEVERITY_LOW


def test_consensus_mode_uses_median():
    resolver = ConflictResolver(
        mode=C.MODE_CONSENSUS, source_priorities=PRIORITIES, numeric_tolerance_pct=0.0001
    )
    bars = [_bar("ZAPI_IDX", 1000.0), _bar("HISTORICAL_PROVIDER", 1100.0),
            _bar("STOCKBIT", 1050.0)]
    result = resolver.resolve(bars)
    close_res = [fr for fr in result.field_resolutions if fr.field_name == "close"][0]
    assert close_res.resolution_reason == "CONSENSUS_MEDIAN"
    assert result.record.close == 1050.0