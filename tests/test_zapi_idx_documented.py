from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.data_sources.base import (
    SourceRateLimited,
    SourceUnavailable,
    SourceUnsupported,
    TransportResponse,
)
from modules.data_sources.config import SourceConfig
from modules.data_sources.zapi_idx_adapter import (
    HttpZapiTransport,
    MockZapiTransport,
    ZapiIdxAdapter,
    ZapiIdxClient,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "zapi_idx"


def _config() -> SourceConfig:
    return SourceConfig(
        name="ZAPI_IDX",
        enabled=True,
        documentation_configured=True,
        api_key_env="ZAPI_IDX_API_KEY",
        base_url_env="ZAPI_IDX_BASE_URL",
    )


def _client(transport: MockZapiTransport | None = None) -> ZapiIdxClient:
    return ZapiIdxClient(transport or MockZapiTransport(), _config())


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_documented_paths_and_query_parameters_are_exact():
    transport = MockZapiTransport()
    client = _client(transport)

    client.fetch_raw("DailyBar", "BBCA", market_date="2026-06-12", length=20, start=4)
    request = transport.requests[-1]
    assert request["path"] == "/stock-summary"
    assert request["params"] == {"length": 20, "start": 4, "date": "2026-06-12", "code": "BBCA"}
    assert "symbol" not in request["params"]

    client.fetch_raw("MarketIndex", "COMPOSITE", date="20260612", length=50, start=0)
    request = transport.requests[-1]
    assert request["path"] == "/index-summary"
    assert request["params"] == {"length": 50, "start": 0, "date": "20260612"}

    client.fetch_raw("TradingStatus", "", activity_type="uma")
    request = transport.requests[-1]
    assert request["path"] == "/market-activity"
    assert request["params"] == {"type": "uma"}


def test_symbol_metadata_calls_both_documented_endpoints_without_date_guess():
    transport = MockZapiTransport()
    client = _client(transport)
    raw = client.fetch_raw("SymbolMetadata", "BBCA", length=20, start=0, sector="Financials", board="Utama")
    assert [item["path"] for item in transport.requests] == ["/companies", "/securities"]
    assert transport.requests[0]["params"] == {"length": 20, "start": 0, "code": "BBCA"}
    assert transport.requests[1]["params"] == {
        "length": 20,
        "start": 0,
        "code": "BBCA",
        "sector": "Financials",
        "board": "Utama",
    }
    assert raw["_zapi_endpoints"] == ["/companies", "/securities"]


def test_unverified_record_types_are_not_routed_to_guessed_paths():
    client = _client()
    with pytest.raises(SourceUnsupported, match="OrderBookSnapshot"):
        client.fetch_raw("OrderBookSnapshot", "BBCA")
    with pytest.raises(SourceUnsupported, match="BrokerFlow"):
        client.fetch_raw("BrokerFlow", "BBCA", date="2026-06-12")
    with pytest.raises(SourceUnsupported, match="IntradayQuote"):
        client.fetch_raw("IntradayQuote", "BBCA")


def test_broker_summary_fixture_is_verified_but_not_enabled_as_broker_flow():
    payload = _fixture("broker_summary.json")
    row = payload["data"][0]
    assert {"IDFirm", "FirmName", "Value", "Volume", "Frequency", "Date"}.issubset(row)
    # The documented shape has no symbol or BUY/SELL/net side, so it remains
    # explicitly unsupported for the canonical BrokerFlow record.
    with pytest.raises(SourceUnsupported, match="BrokerFlow"):
        _client().fetch_raw("BrokerFlow", "BBCA", date="2026-06-12")


def test_stock_summary_fixture_maps_to_canonical_with_endpoint_provenance():
    raw = _fixture("stock_summary.json")
    raw["_zapi_endpoint"] = "/stock-summary"
    record = ZapiIdxAdapter(_client()).to_canonical("DailyBar", raw, symbol="AADI")[0]
    assert record.symbol == "AADI"
    assert record.market_date == "2026-06-12"
    assert record.open == 8100.0
    assert record.close == 8650.0
    assert record.previous_close == 8050.0
    assert record.value == 182271837500.0
    assert record.traded_value == 182271837500.0
    assert record.foreign_buy == 5896500.0
    assert record.foreign_sell == 9874800.0
    assert record.bid == 8625.0
    assert record.bid_volume == 29200.0
    assert record.offer == 8650.0
    assert record.offer_volume == 67500.0
    assert record.source == "ZAPI_IDX_MOCK"
    assert record.source_record_id == "/stock-summary:4031673"
    assert record.field_provenance["__endpoint"]["value"] == "/stock-summary"
    assert record.field_provenance["close"]["source"] == "ZAPI_IDX_MOCK"


def test_live_stock_summary_wrapper_and_actual_schema_are_supported():
    payload = _fixture("stock_summary_live_20260803.json")

    class LiveFixtureTransport(MockZapiTransport):
        def request(self, *args, **kwargs):
            return TransportResponse(status_code=200, payload=payload)

    client = _client(LiveFixtureTransport())
    raw = client.fetch_raw("DailyBar", "", date="20260803", length=1, start=0)
    assert raw["_zapi_status"] == "SUCCESS"
    assert raw["recordsTotal"] == 963
    assert raw["data"][0]["StockCode"] == "AADI"
    record = ZapiIdxAdapter(client).to_canonical("DailyBar", raw)[0]
    assert record.symbol == "AADI"
    assert record.market_date == "2026-08-03"
    assert record.previous_close == 9225.0
    assert record.open == 9300.0
    assert record.high == 9300.0
    assert record.low == 9075.0
    assert record.close == 9100.0
    assert record.volume == 4529500.0
    assert record.traded_value == 41430862500.0
    assert record.frequency == 3273.0
    assert record.foreign_buy == 503600.0
    assert record.foreign_sell == 2396100.0


def test_live_metadata_wrappers_are_unwrapped_for_companies_and_securities():
    class LiveMetadataTransport(MockZapiTransport):
        def request(self, method, path, *, params=None, headers=None, timeout=None):
            if path == "/companies":
                payload = {
                    "project": "idx",
                    "data": {
                        "provider": "idx",
                        "dataset": "companies",
                        "recordsTotal": 1,
                        "recordsFiltered": 1,
                        "length": 1,
                        "start": 0,
                        "data": [{
                            "KodeEmiten": "AADI", "Sektor": "Energi",
                            "SubSektor": "Minyak", "NamaEmiten": "AADI Tbk",
                        }],
                    },
                    "timestamp": "2026-08-04T00:00:00Z",
                }
            else:
                payload = {
                    "project": "idx",
                    "data": {
                        "provider": "idx",
                        "dataset": "securities",
                        "recordsTotal": 1,
                        "recordsFiltered": 1,
                        "length": 1,
                        "start": 0,
                        "data": [{
                            "Code": "AADI", "Name": "AADI Tbk",
                            "ListingBoard": "Utama", "Shares": 1_000_000,
                            "ListingDate": "2000-01-01T00:00:00",
                        }],
                    },
                    "timestamp": "2026-08-04T00:00:00Z",
                }
            return TransportResponse(status_code=200, payload=payload)

    client = _client(LiveMetadataTransport())
    raw = client.fetch_raw("SymbolMetadata", "", length=1, start=0)
    assert raw["companies"]["data"][0]["KodeEmiten"] == "AADI"
    assert raw["securities"]["data"][0]["Code"] == "AADI"
    records = ZapiIdxAdapter(client).to_canonical("SymbolMetadata", raw, market_date="2026-08-04")
    assert records[0].symbol == "AADI"
    assert records[0].sector == "Energi"


def test_valid_empty_stock_summary_is_classified_without_schema_error():
    payload = {"data": {"data": [], "recordsTotal": 0, "recordsFiltered": 0}}

    class EmptyFixtureTransport(MockZapiTransport):
        def request(self, *args, **kwargs):
            return TransportResponse(status_code=200, payload=payload)

    client = _client(EmptyFixtureTransport())
    raw = client.fetch_raw("DailyBar", "", date="20260804", length=1, start=0)
    assert raw["_zapi_status"] == "ZAPI_EMPTY_DATASET"
    assert raw["data"] == []
    assert ZapiIdxAdapter(client).to_canonical("DailyBar", raw) == []


def test_index_fixture_maps_absolute_change_to_percentage():
    raw = _fixture("index_summary.json")
    raw["_zapi_endpoint"] = "/index-summary"
    record = ZapiIdxAdapter(_client()).to_canonical("MarketIndex", raw)[0]
    assert record.index_code == "COMPOSITE"
    assert record.close == 6007.656
    assert record.high == 6074.072
    assert record.low == 5952.852
    assert record.change_pct == pytest.approx(121.624 / 5886.032 * 100)
    assert record.market_date == "2026-06-12"


def test_metadata_and_market_activity_fixtures_map_to_canonical():
    raw = {
        "companies": {**_fixture("companies.json"), "_zapi_endpoint": "/companies"},
        "securities": {**_fixture("securities.json"), "_zapi_endpoint": "/securities"},
        "_zapi_endpoints": ["/companies", "/securities"],
    }
    metadata = ZapiIdxAdapter(_client()).to_canonical("SymbolMetadata", raw, market_date="2026-06-12")
    assert metadata[0].symbol == "AADI"
    assert metadata[0].sector == "Energi"
    assert metadata[0].listed_shares == 7786891760.0
    assert metadata[0].board == "Utama"

    activity = {**_fixture("market_activity.json"), "_zapi_endpoint": "/market-activity"}
    status = ZapiIdxAdapter(_client()).to_canonical("TradingStatus", activity)[0]
    assert status.symbol == "SOFA"
    assert status.status == "UMA"
    assert status.is_tradable is True
    assert status.is_suspended is False


class _Response:
    def __init__(self, status_code: int, payload: object, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = b"1"
        self._payload = payload

    def json(self):
        return self._payload


def test_http_transport_uses_x_api_key_and_never_bearer_header():
    seen: dict[str, object] = {}

    def request(method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs)
        return _Response(200, _fixture("stock_summary.json"))

    transport = HttpZapiTransport(
        "https://api.zpi.web.id/v1/finance:idx",
        "fixture-key",
        connect_timeout=5,
        read_timeout=12,
        request_fn=request,
    )
    response = transport.request(
        "GET",
        "/stock-summary",
        params={"length": 1},
        headers={"Authorization": "", "x-api-key": "other"},
    )
    assert response.status_code == 200
    assert seen["url"] == "https://api.zpi.web.id/v1/finance:idx/stock-summary"
    headers = seen["kwargs"]["headers"]
    assert headers == {"x-api-key": "fixture-key"}
    assert seen["kwargs"]["timeout"] == (5.0, 12.0)


def test_http_transport_exposes_retry_after_for_rate_limit():
    def request(*args, **kwargs):
        return _Response(429, {"message": "too many"}, {"Retry-After": "3"})

    transport = HttpZapiTransport("https://example.test", "key", request_fn=request)
    with pytest.raises(SourceRateLimited) as exc:
        transport.request("GET", "/stock-summary")
    assert exc.value.retry_after == 3.0


def test_invalid_response_shape_is_rejected_before_mapping():
    class InvalidTransport(MockZapiTransport):
        def request(self, *args, **kwargs):
            return TransportResponse(status_code=200, payload={"data": {"unexpected": True}})

    with pytest.raises(SourceUnavailable, match="ZAPI_RESPONSE_INVALID"):
        _client(InvalidTransport()).fetch_raw("DailyBar", "BBCA")


def test_stock_summary_records_total_must_be_an_integer():
    class InvalidCountTransport(MockZapiTransport):
        def request(self, *args, **kwargs):
            return TransportResponse(
                status_code=200,
                payload={"data": {"data": [], "recordsTotal": "0"}},
            )

    with pytest.raises(SourceUnavailable, match="ZAPI_RESPONSE_INVALID"):
        _client(InvalidCountTransport()).fetch_raw("DailyBar", "")
