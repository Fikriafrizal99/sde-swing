from __future__ import annotations

from pathlib import Path

from modules.data_sources.canonical import (
    BrokerFlow,
    DailyBar,
    RECORD_TYPES,
    TradingStatus,
    compute_payload_hash,
    now_wib,
)
from modules.data_sources.config import load_data_source_config
from modules.data_sources.constants import SCHEMA_VERSION, WIB

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_record_has_full_provenance_envelope():
    bar = DailyBar(symbol="BBCA", market_date="2026-01-02", source="ZAPI_IDX")
    data = bar.to_dict()
    for key in (
        "symbol", "market_date", "event_timestamp", "received_at", "source",
        "source_record_id", "freshness_seconds", "quality_status",
        "fallback_used", "conflict_status", "raw_payload_hash", "schema_version",
    ):
        assert key in data, f"missing provenance field {key}"
    assert data["schema_version"] == SCHEMA_VERSION


def test_all_nine_record_types_registered():
    expected = {
        "DailyBar", "IntradayQuote", "OrderBookSnapshot", "BrokerFlow",
        "ForeignFlow", "TradingStatus", "CorporateAction", "MarketIndex",
        "SymbolMetadata",
    }
    assert expected.issubset(set(RECORD_TYPES))


def test_daily_bar_closed_flag_distinguishes_partial_candle():
    closed = DailyBar(symbol="BBCA", market_date="2026-01-02", is_closed=True)
    partial = DailyBar(symbol="BBCA", market_date="2026-01-02", is_closed=False)
    assert closed.is_closed is True
    assert partial.is_closed is False


def test_freshness_uses_wib_timezone():
    now = now_wib()
    assert now.tzinfo is not None
    bar = DailyBar(
        symbol="BBCA",
        market_date="2026-01-02",
        event_timestamp=now.isoformat(),
        received_at=now.isoformat(),
    )
    bar.compute_freshness(at=now)
    assert bar.freshness_seconds is not None
    assert bar.freshness_seconds >= 0.0


def test_field_provenance_recorded_per_field():
    bar = DailyBar(symbol="BBCA", market_date="2026-01-02", close=1000.0)
    bar.set_provenance("close", "ZAPI_IDX", 1000.0)
    assert bar.field_provenance["close"]["source"] == "ZAPI_IDX"
    assert bar.field_provenance["close"]["value"] == 1000.0


def test_payload_hash_is_deterministic():
    a = compute_payload_hash({"x": 1, "y": 2})
    b = compute_payload_hash({"y": 2, "x": 1})
    assert a == b
    assert compute_payload_hash(None) == ""


def test_data_source_config_loads_and_validates():
    cfg = load_data_source_config(ROOT / "config" / "data_sources.json")
    # ZAPI must be disabled/not-configured by default; never live.
    zapi = cfg.source("ZAPI_IDX")
    assert zapi is not None
    assert zapi.enabled is False
    assert zapi.documentation_configured is False
    # Ownership chains resolve to known sources.
    assert cfg.resolution_chain("DailyBar")[0] == "ZAPI_IDX"
    assert cfg.resolution_chain("BrokerFlow") == ["STOCKBIT"]
    # Technical indicators are internal only.
    assert cfg.ownership_for("TechnicalIndicator").primary == "INTERNAL"


def test_api_key_only_from_environment(monkeypatch):
    cfg = load_data_source_config(ROOT / "config" / "data_sources.json")
    zapi = cfg.source("ZAPI_IDX")
    monkeypatch.delenv("ZAPI_IDX_API_KEY", raising=False)
    assert zapi.api_key() is None
    monkeypatch.setenv("ZAPI_IDX_API_KEY", "secret-from-env")
    assert zapi.api_key() == "secret-from-env"