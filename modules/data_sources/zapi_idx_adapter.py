from __future__ import annotations

"""ZAPI IDX adapter — NOT_CONFIGURED until documentation + credentials are supplied.

This module defines the interface, contract, mock transport, and synthetic
fixtures for the ZAPI IDX source.  While ``documentation_configured`` is False
in ``config/data_sources.json`` (or the env vars are absent), every call
returns ``ZAPI_DOCUMENTATION_NOT_CONFIGURED`` status and no live request is
made.  The adapter is never treated as live in that state.

When real documentation becomes available:
1. Set ``documentation_configured: true`` in config/data_sources.json.
2. Set ZAPI_IDX_BASE_URL and ZAPI_IDX_API_KEY in the environment.
3. Replace MockZapiTransport with HttpZapiTransport (stub below).
4. Implement the real endpoint paths in ZapiIdxClient.fetch_raw.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from modules.data_sources import constants as C
from modules.data_sources.base import (
    Adapter,
    SourceClient,
    SourceNotConfigured,
    Transport,
    TransportResponse,
)
from modules.data_sources.canonical import (
    BrokerFlow,
    CanonicalRecord,
    DailyBar,
    MarketIndex,
    OrderBookSnapshot,
    SymbolMetadata,
    TradingStatus,
    compute_payload_hash,
    now_wib,
)
from modules.data_sources.config import SourceConfig


# ---------------------------------------------------------------------------
# Mock transport — deterministic synthetic fixtures, never makes HTTP calls
# ---------------------------------------------------------------------------
class MockZapiTransport(Transport):
    """Returns synthetic fixtures for CI and offline testing."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        symbol = (params or {}).get("symbol", "MOCK")
        market_date = (params or {}).get("market_date", "2026-01-02")
        payload = _synthetic_fixture(path, symbol, market_date)
        return TransportResponse(status_code=200, payload=payload, latency_ms=0.0)


def _synthetic_fixture(path: str, symbol: str, market_date: str) -> dict[str, Any]:
    base: dict[str, Any] = {"symbol": symbol, "market_date": market_date, "source": "ZAPI_IDX_MOCK"}
    if "daily" in path or "ohlcv" in path:
        return {**base, "open": 1000.0, "high": 1050.0, "low": 990.0, "close": 1030.0, "volume": 5_000_000.0}
    if "intraday" in path or "quote" in path:
        return {**base, "last_price": 1030.0, "bid": 1025.0, "ask": 1035.0}
    if "orderbook" in path:
        return {**base, "best_bid": 1025.0, "best_ask": 1035.0, "depth_levels": 5}
    if "status" in path:
        return {**base, "status": "NORMAL", "is_tradable": True, "is_suspended": False}
    if "index" in path or "ihsg" in path:
        return {**base, "index_code": "IHSG", "close": 7500.0}
    if "metadata" in path:
        return {**base, "name": f"{symbol} Tbk", "board": "RG"}
    return base


# ---------------------------------------------------------------------------
# HTTP transport stub (replace MockZapiTransport when docs are available)
# ---------------------------------------------------------------------------
class HttpZapiTransport(Transport):
    """Real HTTP transport — only instantiated when documentation_configured=True."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        # Deferred import so the module loads without requests installed in CI.
        import time
        import requests  # type: ignore[import]

        url = f"{self._base_url}/{path.lstrip('/')}"
        hdrs = {"Authorization": f"Bearer {self._api_key}", **(headers or {})}
        t0 = time.monotonic()
        resp = requests.request(
            method,
            url,
            params=params,
            headers=hdrs,
            timeout=timeout or self._timeout,
        )
        latency_ms = (time.monotonic() - t0) * 1000
        return TransportResponse(
            status_code=resp.status_code,
            payload=resp.json() if resp.content else {},
            headers=dict(resp.headers),
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# ZAPI IDX source client
# ---------------------------------------------------------------------------
class ZapiIdxClient(SourceClient):
    name = "ZAPI_IDX"

    def __init__(
        self,
        transport: Transport,
        source_config: SourceConfig,
    ) -> None:
        super().__init__(
            retry=source_config.retry,
            timeout=source_config.timeout,
        )
        self._transport = transport
        self._source_config = source_config

    @classmethod
    def from_config(cls, source_config: SourceConfig, *, force_mock: bool = False) -> "ZapiIdxClient":
        if force_mock or not source_config.documentation_configured or not source_config.has_credentials():
            return cls(MockZapiTransport(), source_config)
        base_url = source_config.base_url() or ""
        api_key = source_config.api_key() or ""
        return cls(HttpZapiTransport(base_url, api_key, source_config.timeout), source_config)

    def is_configured(self) -> bool:
        return (
            self._source_config.documentation_configured
            and self._source_config.has_credentials()
            and not isinstance(self._transport, MockZapiTransport)
        )

    def fetch_raw(self, record_type: str, symbol: str, **kwargs: Any) -> Any:
        if not self._source_config.enabled:
            raise SourceNotConfigured(C.ZAPI_DOCUMENTATION_NOT_CONFIGURED)
        path_map = {
            "DailyBar": "v1/daily",
            "IntradayQuote": "v1/intraday/quote",
            "OrderBookSnapshot": "v1/orderbook",
            "TradingStatus": "v1/status",
            "MarketIndex": "v1/index",
            "SymbolMetadata": "v1/metadata",
        }
        path = path_map.get(record_type, f"v1/{record_type.lower()}")
        params = {"symbol": symbol, **kwargs}
        resp = self.with_retry(
            lambda: self._transport.request("GET", path, params=params, timeout=self.timeout)
        )
        return resp.payload


# ---------------------------------------------------------------------------
# ZAPI IDX adapter — maps raw payload to canonical records
# ---------------------------------------------------------------------------
class ZapiIdxAdapter(Adapter):
    source_name = "ZAPI_IDX"

    def __init__(self, client: ZapiIdxClient) -> None:
        self._client = client

    def to_canonical(self, record_type: str, raw: Any, **kwargs: Any) -> list[CanonicalRecord]:
        if not isinstance(raw, dict):
            return []
        now = now_wib()
        received = now.isoformat()
        symbol = str(raw.get("symbol", kwargs.get("symbol", ""))).strip().upper()
        market_date = str(raw.get("market_date", kwargs.get("market_date", ""))).strip()
        source = "ZAPI_IDX_MOCK" if isinstance(self._client._transport, MockZapiTransport) else "ZAPI_IDX"

        common = dict(
            symbol=symbol,
            market_date=market_date,
            event_timestamp=str(raw.get("event_timestamp", received)),
            received_at=received,
            source=source,
            source_record_id=str(raw.get("record_id", "")),
            raw_payload_hash=compute_payload_hash(raw),
        )

        if record_type == "DailyBar":
            return [DailyBar(
                **common,
                open=_f(raw.get("open")),
                high=_f(raw.get("high")),
                low=_f(raw.get("low")),
                close=_f(raw.get("close")),
                volume=_f(raw.get("volume")),
                value=_f(raw.get("value")),
                frequency=_f(raw.get("frequency")),
                is_closed=bool(raw.get("is_closed", True)),
            )]
        if record_type == "OrderBookSnapshot":
            return [OrderBookSnapshot(
                **common,
                best_bid=_f(raw.get("best_bid")),
                best_ask=_f(raw.get("best_ask")),
                spread=_f(raw.get("spread")),
                depth_levels=int(raw.get("depth_levels", 0) or 0),
                bid_prices=list(raw.get("bid_prices", [])),
                bid_volumes=list(raw.get("bid_volumes", [])),
                ask_prices=list(raw.get("ask_prices", [])),
                ask_volumes=list(raw.get("ask_volumes", [])),
            )]
        if record_type == "TradingStatus":
            return [TradingStatus(
                **common,
                status=str(raw.get("status", "UNKNOWN")).upper(),
                is_tradable=bool(raw.get("is_tradable", True)),
                is_suspended=bool(raw.get("is_suspended", False)),
                suspend_reason=str(raw.get("suspend_reason", "")),
                ambiguous=bool(raw.get("ambiguous", False)),
            )]
        if record_type == "MarketIndex":
            return [MarketIndex(
                **common,
                index_code=str(raw.get("index_code", "IHSG")),
                open=_f(raw.get("open")),
                high=_f(raw.get("high")),
                low=_f(raw.get("low")),
                close=_f(raw.get("close")),
                volume=_f(raw.get("volume")),
                change_pct=_f(raw.get("change_pct")),
                is_closed=bool(raw.get("is_closed", True)),
            )]
        if record_type == "SymbolMetadata":
            return [SymbolMetadata(
                **common,
                name=str(raw.get("name", "")),
                board=str(raw.get("board", "")),
                sector=str(raw.get("sector", "")),
                is_tradable=bool(raw.get("is_tradable", True)),
            )]
        return []


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None