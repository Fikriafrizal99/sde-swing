from __future__ import annotations

import pytest

from modules.data_sources import constants as C
from modules.data_sources.base import SourceNotConfigured
from modules.data_sources.config import SourceConfig
from modules.data_sources.zapi_idx_adapter import (
    MockZapiTransport,
    ZapiIdxAdapter,
    ZapiIdxClient,
)


def _disabled_config():
    # Mirrors config/data_sources.json defaults: disabled + not documented.
    return SourceConfig(
        name="ZAPI_IDX",
        enabled=False,
        documentation_configured=False,
        api_key_env="ZAPI_IDX_API_KEY",
        base_url_env="ZAPI_IDX_BASE_URL",
    )


def _enabled_mock_config():
    # Enabled for fetching but still without docs/creds → forced to mock.
    return SourceConfig(
        name="ZAPI_IDX",
        enabled=True,
        documentation_configured=False,
        api_key_env="ZAPI_IDX_API_KEY",
        base_url_env="ZAPI_IDX_BASE_URL",
    )


def test_from_config_uses_mock_when_not_documented(monkeypatch):
    monkeypatch.delenv("ZAPI_IDX_API_KEY", raising=False)
    monkeypatch.delenv("ZAPI_IDX_BASE_URL", raising=False)
    client = ZapiIdxClient.from_config(_enabled_mock_config())
    assert isinstance(client._transport, MockZapiTransport)
    # Never reports itself as configured/live in this state.
    assert client.is_configured() is False


def test_from_config_forces_mock_even_with_env(monkeypatch):
    # Credentials present but documentation not configured → still mock.
    monkeypatch.setenv("ZAPI_IDX_API_KEY", "k")
    monkeypatch.setenv("ZAPI_IDX_BASE_URL", "https://example.test")
    client = ZapiIdxClient.from_config(_enabled_mock_config())
    assert isinstance(client._transport, MockZapiTransport)
    assert client.is_configured() is False


def test_disabled_source_raises_not_configured():
    client = ZapiIdxClient.from_config(_disabled_config())
    with pytest.raises(SourceNotConfigured) as exc:
        client.fetch_raw("DailyBar", "BBCA", market_date="2026-01-02")
    assert C.ZAPI_DOCUMENTATION_NOT_CONFIGURED in str(exc.value)


def test_mock_daily_bar_maps_to_canonical():
    cfg = _enabled_mock_config()
    client = ZapiIdxClient.from_config(cfg, force_mock=True)
    raw = client.fetch_raw("DailyBar", "BBCA", market_date="2026-01-02")
    adapter = ZapiIdxAdapter(client)
    records = adapter.to_canonical("DailyBar", raw, symbol="BBCA")
    assert len(records) == 1
    bar = records[0]
    assert bar.record_type == "DailyBar"
    assert bar.symbol == "BBCA"
    # Mock provenance must be flagged distinctly — never plain ZAPI_IDX (live).
    assert bar.source == "ZAPI_IDX_MOCK"
    assert bar.close == 1030.0
    assert bar.raw_payload_hash  # deterministic hash recorded


def test_mock_trading_status_maps_to_canonical():
    client = ZapiIdxClient.from_config(_enabled_mock_config(), force_mock=True)
    raw = client.fetch_raw("TradingStatus", "BBCA", market_date="2026-01-02")
    adapter = ZapiIdxAdapter(client)
    records = adapter.to_canonical("TradingStatus", raw, symbol="BBCA")
    assert records[0].record_type == "TradingStatus"
    # The documented market-activity default is type=suspend, so a returned
    # row is fail-closed as suspended rather than a fabricated NORMAL state.
    assert records[0].status == "SUSPENDED"


def test_force_mock_never_makes_http():
    # MockZapiTransport returns latency 0 and status 200 without network.
    transport = MockZapiTransport()
    resp = transport.request("GET", "/stock-summary", params={"code": "BBCA"})
    assert resp.status_code == 200
    assert resp.latency_ms == 0.0
