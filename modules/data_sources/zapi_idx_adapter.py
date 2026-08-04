from __future__ import annotations

"""ZAPI IDX adapter backed by the documented Zapi Finance IDX API.

Only endpoints present in the published IDX reference are represented here.
The old ``v1/daily``/``v1/index``/``v1/status``/``v1/metadata`` and
``v1/orderbook`` paths are deliberately absent: an unverified record type is
reported as unsupported instead of being sent to a guessed URL.

Documentation reference (accessed 2026-08-03):
https://zpi.web.id/api/finance/idx/llms.txt
"""

from dataclasses import dataclass
from datetime import date, datetime
import re
import time
from typing import Any, Mapping

from modules.data_sources import constants as C
from modules.data_sources.base import (
    Adapter,
    SourceClient,
    SourceNotConfigured,
    SourceRateLimited,
    SourceRequestInvalid,
    SourceTimeout,
    SourceUnavailable,
    SourceUnsupported,
    Transport,
    TransportResponse,
)
from modules.data_sources.canonical import (
    CanonicalRecord,
    DailyBar,
    MarketIndex,
    SymbolMetadata,
    TradingStatus,
    compute_payload_hash,
    now_wib,
)
from modules.data_sources.config import SourceConfig


@dataclass(frozen=True)
class ZapiEndpoint:
    """Verified endpoint and the query fields accepted by that endpoint."""

    path: str
    query_params: tuple[str, ...]
    response_shape: str  # ``list`` or ``activity``


# These are the only record types for which this adapter will make a request.
# ``BrokerSummary`` is documented, but its rows are aggregate broker totals
# without symbol/BUY/SELL/net fields, so it cannot safely become BrokerFlow.
ZAPI_ENDPOINTS: dict[str, tuple[ZapiEndpoint, ...]] = {
    "DailyBar": (
        ZapiEndpoint("/stock-summary", ("length", "start", "date", "code"), "list"),
    ),
    "MarketIndex": (
        ZapiEndpoint("/index-summary", ("length", "start", "date"), "list"),
    ),
    "SymbolMetadata": (
        ZapiEndpoint("/companies", ("length", "start", "code"), "list"),
        ZapiEndpoint("/securities", ("length", "start", "code", "sector", "board"), "list"),
    ),
    "TradingStatus": (
        ZapiEndpoint("/market-activity", ("type",), "activity"),
    ),
}

ZAPI_DOCUMENTED_BROKER_ENDPOINT = ZapiEndpoint(
    "/broker-summary", ("length", "start", "date"), "list"
)

ZAPI_UNSUPPORTED_RECORD_TYPES: dict[str, str] = {
    "IntradayQuote": "no documented ZAPI IDX quote endpoint",
    "OrderBookSnapshot": "ZAPI IDX catalog has no order-book endpoint",
    "BrokerFlow": "broker-summary has aggregate broker totals, not symbol BUY/SELL flow",
    "ForeignFlow": "foreign-flow is not enabled until its canonical mapping is verified",
    "CorporateAction": "no verified corporate-action mapping is configured",
}

_DATE_RE = re.compile(r"^(?:\d{8}|\d{4}-\d{2}-\d{2})$")


# ---------------------------------------------------------------------------
# Mock transport — deterministic, documented-shaped fixtures only
# ---------------------------------------------------------------------------
class MockZapiTransport(Transport):
    """Offline transport; it never makes an HTTP request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        request = {
            "method": method,
            "path": path,
            "params": dict(params or {}),
            "headers": dict(headers or {}),
            "timeout": timeout,
        }
        self.requests.append(request)
        payload = _synthetic_fixture(path, request["params"])
        return TransportResponse(status_code=200, payload=payload, latency_ms=0.0)


def _synthetic_fixture(path: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Small fixtures that follow the published Zapi field names."""

    clean_path = "/" + path.strip("/")
    requested_date = str(params.get("date") or "2026-01-02")
    code = str(params.get("code") or "BBCA").upper()
    if clean_path.endswith("/stock-summary"):
        return {
            "data": [{
                "No": 1,
                "Bid": 1025,
                "Low": 990,
                "Date": f"{requested_date[:10]}T00:00:00",
                "High": 1050,
                "Close": 1030,
                "Offer": 1035,
                "Value": 5_000_000_000,
                "Change": 30,
                "Volume": 5_000_000,
                "Previous": 1000,
                "BidVolume": 1000,
                "Frequency": 500,
                "OpenPrice": 1000,
                "StockCode": code,
                "StockName": f"{code} Tbk",
                "FirstTrade": 1000,
                "ForeignBuy": 0,
                "ForeignSell": 0,
                "OfferVolume": 1200,
                "IDStockSummary": 1,
            }],
            "start": int(params.get("start", 0) or 0),
            "length": 1,
            "dataset": "stock-summary",
            "provider": "idx",
            "recordsTotal": 1,
            "recordsFiltered": 1,
        }
    if clean_path.endswith("/index-summary"):
        return {
            "data": [{
                "No": 1,
                "Date": f"{requested_date[:10]}T00:00:00",
                "Close": 7500.0,
                "Value": 21_654_846_683_780,
                "Change": 121.624,
                "Lowest": 7450.0,
                "Volume": 34_775_696_450,
                "Highest": 7600.0,
                "Previous": 7378.376,
                "Frequency": 2_346_364,
                "IndexCode": "COMPOSITE",
                "MarketCapital": 10_524_297_346_477_400,
                "NumberOfStock": 913,
                "IndexSummaryID": 1,
            }],
            "dataset": "index-summary",
            "provider": "idx",
            "recordsTotal": 1,
        }
    if clean_path.endswith("/companies"):
        return {
            "data": [{
                "id": 1,
                "Sektor": "Keuangan",
                "SubSektor": "Bank",
                "KodeEmiten": code,
                "NamaEmiten": f"{code} Tbk",
                "PapanPencatatan": "Utama",
                "Status": 0,
                "EfekEmiten_Saham": True,
                "DataID": 1,
            }],
            "start": int(params.get("start", 0) or 0),
            "length": 1,
            "dataset": "listed-companies",
            "provider": "idx",
            "recordsTotal": 1,
            "recordsFiltered": 1,
        }
    if clean_path.endswith("/securities"):
        return {
            "data": [{
                "Code": code,
                "Name": f"{code} Tbk",
                "Shares": 1_000_000_000,
                "ListingDate": "2000-01-01T00:00:00",
                "ListingBoard": "Utama",
            }],
            "start": int(params.get("start", 0) or 0),
            "length": 1,
            "dataset": "securities",
            "provider": "idx",
            "recordsTotal": 1,
            "recordsFiltered": 1,
        }
    if clean_path.endswith("/market-activity"):
        activity_type = str(params.get("type") or "suspend").lower()
        return {
            "data": {
                "Results": [{
                    "Judul": f"{activity_type.upper()} {code}",
                    "Status": None,
                    "UMADate": f"{requested_date[:10]}T00:00:00",
                    "CompanyID": code,
                    "CompanyName": f"{code} Tbk",
                    "UMAID": 1,
                    "AnnouncementNo": "MOCK-1",
                }],
                "ResultCount": 1,
                "SearchCriteria": {"pagesize": 10, "indexfrom": 0},
            },
            "type": activity_type,
            "dataset": "market-activity",
            "provider": "idx",
        }
    # Unknown paths are retained as a benign fixture for old transport-only
    # tests; the client never routes a record type to this branch.
    return {"data": [], "dataset": "unknown", "provider": "idx"}


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------
class HttpZapiTransport(Transport):
    """HTTP transport using the documented ``x-api-key`` header."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 15.0,
        *,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        request_fn: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = (
            float(connect_timeout if connect_timeout is not None else timeout),
            float(read_timeout if read_timeout is not None else timeout),
        )
        self._request_fn = request_fn

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        import requests  # type: ignore[import]

        if not self._base_url or not self._api_key:
            raise SourceNotConfigured(C.ZAPI_DOCUMENTATION_NOT_CONFIGURED)
        url = f"{self._base_url}/{path.lstrip('/')}"
        # Caller headers are allowed for content negotiation, but cannot
        # replace the credential source or accidentally add a bearer token.
        hdrs = {**(headers or {}), "x-api-key": self._api_key}
        hdrs.pop("Authorization", None)
        t0 = time.monotonic()
        request_fn = self._request_fn or requests.request
        try:
            resp = request_fn(
                method,
                url,
                params=params,
                headers=hdrs,
                timeout=timeout or self._timeout,
            )
        except requests.Timeout as exc:
            raise SourceTimeout("ZAPI request timed out") from exc
        except requests.RequestException as exc:
            raise SourceUnavailable("ZAPI request failed") from exc
        latency_ms = (time.monotonic() - t0) * 1000

        if resp.status_code == 429:
            retry_after = _retry_after_seconds(getattr(resp, "headers", {}).get("Retry-After"))
            raise SourceRateLimited(C.ZAPI_RATE_LIMITED, retry_after=retry_after)
        if resp.status_code in {401, 403}:
            raise SourceUnavailable(C.ZAPI_AUTH_FAILED)
        if resp.status_code in {404, 405}:
            raise SourceUnsupported(f"{C.ZAPI_ENDPOINT_UNSUPPORTED}:{path}")
        if 400 <= resp.status_code < 500:
            raise SourceRequestInvalid(f"ZAPI HTTP {resp.status_code}")
        if 500 <= resp.status_code:
            raise SourceUnavailable(f"ZAPI HTTP {resp.status_code}")

        content_type = str(dict(getattr(resp, "headers", {}) or {}).get("Content-Type", ""))
        if content_type and "json" not in content_type.lower():
            raise SourceUnavailable(f"{C.ZAPI_RESPONSE_INVALID}:content-type")

        try:
            payload = resp.json() if getattr(resp, "content", b"") else {}
        except (TypeError, ValueError) as exc:
            raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID) from exc
        if not isinstance(payload, (dict, list)):
            raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID)
        return TransportResponse(
            status_code=resp.status_code,
            payload=payload,
            headers={str(k): str(v) for k, v in dict(getattr(resp, "headers", {}) or {}).items()},
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# ZAPI IDX source client
# ---------------------------------------------------------------------------
class ZapiIdxClient(SourceClient):
    name = "ZAPI_IDX"

    def __init__(self, transport: Transport, source_config: SourceConfig, *, explicit_mock: bool | None = None) -> None:
        super().__init__(
            retry=source_config.retry,
            timeout=source_config.timeout,
            backoff_base=source_config.backoff_base_seconds,
        )
        self._transport = transport
        self._source_config = source_config
        self._explicit_mock = isinstance(transport, MockZapiTransport) if explicit_mock is None else bool(explicit_mock)
        self.last_response: dict[str, Any] = {}
        self.request_attempt_count = 0
        self._event_callback: Any | None = None
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, dict[str, Any]]] = {}
        self._last_request_at = 0.0

    @classmethod
    def from_config(cls, source_config: SourceConfig, *, force_mock: bool = False) -> "ZapiIdxClient":
        if (
            force_mock
            or not source_config.enabled
            or not source_config.documentation_configured
            or not source_config.has_credentials()
        ):
            return cls(MockZapiTransport(), source_config, explicit_mock=force_mock)
        return cls(
            HttpZapiTransport(
                source_config.base_url() or "",
                source_config.api_key() or "",
                source_config.timeout,
                connect_timeout=source_config.connect_timeout_seconds,
                read_timeout=source_config.read_timeout_seconds,
            ),
            source_config,
        )

    def is_configured(self) -> bool:
        return bool(
            self._source_config.enabled
            and self._source_config.documentation_configured
            and self._source_config.has_credentials()
            and not isinstance(self._transport, MockZapiTransport)
        )

    def set_event_callback(self, callback: Any | None) -> None:
        self._event_callback = callback

    def _emit(self, event: str, **detail: Any) -> None:
        if self._event_callback is not None:
            self._event_callback(event, detail)

    def fetch_raw(self, record_type: str, symbol: str, **kwargs: Any) -> Any:
        if not self._source_config.enabled:
            reason = (
                C.ZAPI_DOCUMENTATION_NOT_CONFIGURED
                if not self._source_config.documentation_configured
                else "ZAPI_SOURCE_DISABLED"
            )
            raise SourceNotConfigured(reason)
        if not self.is_configured() and not (self._explicit_mock or kwargs.pop("allow_mock", False)):
            raise SourceNotConfigured("ZAPI_CREDENTIALS_NOT_CONFIGURED")
        specs = ZAPI_ENDPOINTS.get(record_type)
        if specs is None:
            reason = ZAPI_UNSUPPORTED_RECORD_TYPES.get(record_type, "no verified endpoint")
            raise SourceUnsupported(f"{C.ZAPI_ENDPOINT_UNSUPPORTED}:{record_type}:{reason}")

        if record_type == "SymbolMetadata":
            company = self._fetch_endpoint(specs[0], symbol, kwargs)
            security = self._fetch_endpoint(specs[1], symbol, kwargs)
            return {
                "companies": company,
                "securities": security,
                "_zapi_endpoints": [specs[0].path, specs[1].path],
            }
        return self._fetch_endpoint(specs[0], symbol, kwargs)

    def _fetch_endpoint(
        self,
        spec: ZapiEndpoint,
        symbol: str,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        params = _build_params(spec, symbol, kwargs)
        cache_key = (spec.path, tuple(sorted((str(key), str(value)) for key, value in params.items())))
        cached = self._cache.get(cache_key)
        if cached and self._source_config.cache_ttl_seconds > 0:
            if time.monotonic() - cached[0] <= self._source_config.cache_ttl_seconds:
                return dict(cached[1])
        rate = self._source_config.rate_limit_per_second
        if rate > 0 and not isinstance(self._transport, MockZapiTransport):
            remaining = (1.0 / rate) - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        def request_once() -> TransportResponse:
            self.request_attempt_count += 1
            request_timeout: Any = self.timeout
            if isinstance(self._transport, HttpZapiTransport):
                request_timeout = self._transport._timeout
            return self._transport.request("GET", spec.path, params=params, timeout=request_timeout)

        response = self.with_retry(
            request_once,
            on_retry=lambda attempt, delay, exc: self._emit(
                "ZAPI_RETRY",
                attempt=attempt,
                max_retries=self.retry,
                backoff_seconds=delay,
                error_type=type(exc).__name__,
            ),
        )
        self._last_request_at = time.monotonic()
        body = _unwrap_payload(response.payload)
        _validate_response(spec, body)
        if not isinstance(body, dict):  # validated specs currently return objects
            raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID)
        result = dict(body)
        result["_zapi_endpoint"] = spec.path
        self.last_response = {
            "endpoint": spec.path,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "latency_ms": response.latency_ms,
            "params": dict(params),
            "pagination": {
                key: result.get(key)
                for key in ("start", "length", "recordsTotal", "recordsFiltered")
                if key in result
            },
        }
        self._cache[cache_key] = (time.monotonic(), dict(result))
        return result


# ---------------------------------------------------------------------------
# Canonical mapping
# ---------------------------------------------------------------------------
class ZapiIdxAdapter(Adapter):
    source_name = "ZAPI_IDX"

    def __init__(self, client: ZapiIdxClient) -> None:
        self._client = client

    def to_canonical(self, record_type: str, raw: Any, **kwargs: Any) -> list[CanonicalRecord]:
        if not isinstance(raw, Mapping):
            return []
        # Keep the mapper safe when a caller supplies the optional documented
        # {status, message, content} envelope directly instead of going
        # through ZapiIdxClient.fetch_raw.
        try:
            unwrapped = _unwrap_payload(raw)
        except SourceUnavailable:
            return []
        if not isinstance(unwrapped, Mapping):
            return []
        raw = unwrapped
        if record_type == "DailyBar":
            return self._daily_bars(raw, kwargs)
        if record_type == "MarketIndex":
            return self._market_indices(raw, kwargs)
        if record_type == "SymbolMetadata":
            return self._metadata(raw, kwargs)
        if record_type == "TradingStatus":
            return self._trading_status(raw, kwargs)
        return []

    @property
    def _source(self) -> str:
        return "ZAPI_IDX_MOCK" if isinstance(self._client._transport, MockZapiTransport) else "ZAPI_IDX"

    def _daily_bars(self, raw: Mapping[str, Any], kwargs: Mapping[str, Any]) -> list[DailyBar]:
        endpoint = str(raw.get("_zapi_endpoint") or "/stock-summary")
        records: list[DailyBar] = []
        for row in _rows(raw):
            symbol = canonical_symbol(row.get("StockCode") or kwargs.get("symbol"))
            event = _timestamp(row.get("Date"))
            market_date = _market_date(row.get("Date"), kwargs.get("market_date"))
            received = now_wib().isoformat()
            rec = DailyBar(
                **_common(self._source, endpoint, symbol, market_date, event, received, row, _text(row.get("IDStockSummary"))),
                open=_f(row.get("OpenPrice")),
                high=_f(row.get("High")),
                low=_f(row.get("Low")),
                close=_f(row.get("Close")),
                volume=_f(row.get("Volume")),
                value=_f(row.get("Value")),
                frequency=_f(row.get("Frequency")),
                previous_close=_f(row.get("Previous")),
                is_closed=True,
            )
            _set_field_provenance(rec, endpoint, event)
            records.append(rec)
        return records

    def _market_indices(self, raw: Mapping[str, Any], kwargs: Mapping[str, Any]) -> list[MarketIndex]:
        endpoint = str(raw.get("_zapi_endpoint") or "/index-summary")
        records: list[MarketIndex] = []
        for row in _rows(raw):
            code = _text(row.get("IndexCode") or provider_symbol(kwargs.get("symbol") or "IHSG", record_type="MarketIndex")).upper()
            requested_symbol = canonical_symbol(kwargs.get("symbol") or code)
            event = _timestamp(row.get("Date"))
            market_date = _market_date(row.get("Date"), kwargs.get("market_date"))
            previous = _f(row.get("Previous"))
            change = _f(row.get("Change"))
            received = now_wib().isoformat()
            rec = MarketIndex(
                **_common(self._source, endpoint, requested_symbol, market_date, event, received, row, _text(row.get("IndexSummaryID"))),
                index_code=code,
                open=None,
                high=_f(row.get("Highest")),
                low=_f(row.get("Lowest")),
                close=_f(row.get("Close")),
                volume=_f(row.get("Volume")),
                value=_f(row.get("Value")),
                change_pct=_pct(change, previous),
                is_closed=True,
            )
            _set_field_provenance(rec, endpoint, event)
            records.append(rec)
        return records

    def _metadata(self, raw: Mapping[str, Any], kwargs: Mapping[str, Any]) -> list[SymbolMetadata]:
        companies = _rows(raw.get("companies") if isinstance(raw.get("companies"), Mapping) else {})
        securities = _rows(raw.get("securities") if isinstance(raw.get("securities"), Mapping) else {})
        company_by_code = {
            canonical_symbol(row.get("KodeEmiten") or row.get("Code")): row for row in companies
        }
        endpoints = raw.get("_zapi_endpoints") or ["/companies", "/securities"]
        endpoint = ",".join(str(item) for item in endpoints)
        records: list[SymbolMetadata] = []
        snapshot_date = _market_date(None, kwargs.get("market_date")) or now_wib().date().isoformat()
        received = now_wib().isoformat()
        for security in securities:
            code = canonical_symbol(security.get("Code") or security.get("KodeEmiten"))
            company = company_by_code.get(code, {})
            event = _timestamp(security.get("ListingDate")) or received
            rec = SymbolMetadata(
                **_common(self._source, endpoint, code, snapshot_date, event, received, {**company, **security}, code),
                name=_text(security.get("Name") or company.get("NamaEmiten")),
                board=_text(security.get("ListingBoard") or company.get("PapanPencatatan")),
                sector=_text(company.get("Sektor")),
                sub_sector=_text(company.get("SubSektor")),
                listed_shares=_f(security.get("Shares")),
                is_tradable=_tradable(company),
            )
            _set_field_provenance(rec, endpoint, event)
            records.append(rec)
        return records

    def _trading_status(self, raw: Mapping[str, Any], kwargs: Mapping[str, Any]) -> list[TradingStatus]:
        endpoint = str(raw.get("_zapi_endpoint") or "/market-activity")
        activity_type = _text(raw.get("type") or kwargs.get("activity_type") or "suspend").lower()
        records: list[TradingStatus] = []
        data = raw.get("data") if isinstance(raw.get("data"), Mapping) else raw
        result_rows = data.get("Results", []) if isinstance(data, Mapping) else []
        if not isinstance(result_rows, list):
            return []
        for row in result_rows:
            if not isinstance(row, Mapping):
                continue
            symbol = canonical_symbol(row.get("CompanyID") or kwargs.get("symbol"))
            event = _timestamp(row.get("UMADate") or row.get("Date"))
            market_date = _market_date(row.get("UMADate") or row.get("Date"), kwargs.get("market_date"))
            status, tradable, suspended = _activity_status(activity_type, row.get("Status"))
            received = now_wib().isoformat()
            source_id = _text(row.get("UMAID") or row.get("AnnouncementNo") or symbol)
            rec = TradingStatus(
                **_common(self._source, endpoint, symbol, market_date, event, received, row, source_id),
                status=status,
                is_tradable=tradable,
                is_suspended=suspended,
                suspend_reason=_text(row.get("Judul") or row.get("CompanyName")),
                ambiguous=False,
            )
            _set_field_provenance(rec, endpoint, event)
            records.append(rec)
        return records


# ---------------------------------------------------------------------------
# Request/response helpers
# ---------------------------------------------------------------------------
def _build_params(spec: ZapiEndpoint, symbol: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    allowed = set(spec.query_params)
    params: dict[str, Any] = {}
    if "length" in allowed and kwargs.get("length") is not None:
        params["length"] = _positive_int("length", kwargs["length"], allow_zero=False)
    if "start" in allowed and kwargs.get("start") is not None:
        params["start"] = _positive_int("start", kwargs["start"], allow_zero=True)
    if "date" in allowed:
        value = kwargs.get("date", kwargs.get("market_date"))
        if value is not None and value != "":
            params["date"] = _date_param(value)
    if "code" in allowed:
        value = kwargs.get("code") or provider_symbol(symbol, record_type="DailyBar")
        if value:
            params["code"] = _text(value).upper()
    if "sector" in allowed and kwargs.get("sector"):
        params["sector"] = _text(kwargs["sector"])
    if "board" in allowed and kwargs.get("board"):
        params["board"] = _text(kwargs["board"])
    if "type" in allowed:
        value = _text(kwargs.get("type") or kwargs.get("activity_type") or "suspend").lower()
        if value not in {"suspend", "relisting", "uma"}:
            raise SourceRequestInvalid("ZAPI market-activity type must be suspend, relisting, or uma")
        params["type"] = value
    return params


def canonical_symbol(value: Any) -> str:
    """Normalize Yahoo/ZAPI/IDX symbol forms into the engine symbol."""
    text = _text(value).upper()
    if text.startswith("IDX:"):
        text = text[4:]
    if text.endswith(".JK"):
        text = text[:-3]
    if text in {"^JKSE", "JKSE", "IHSG", "COMPOSITE"}:
        return "IHSG"
    return text


def provider_symbol(value: Any, *, record_type: str = "DailyBar") -> str:
    """Map a canonical engine symbol to the documented ZAPI request value."""
    symbol = canonical_symbol(value)
    if record_type == "MarketIndex" and symbol == "IHSG":
        return "COMPOSITE"
    return symbol


def _unwrap_payload(payload: Any) -> Any:
    if not isinstance(payload, (Mapping, list)):
        raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID)
    if isinstance(payload, Mapping):
        status = payload.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
            raise SourceUnavailable("ZAPI_API_ERROR")
        if status is False:
            raise SourceUnavailable("ZAPI_API_ERROR")
        if isinstance(status, (int, float)) and status >= 400:
            raise SourceUnavailable("ZAPI_API_ERROR")
        # The reference describes an optional {status,message,content}
        # envelope, while endpoint examples expose {data,...} directly.
        if "content" in payload and isinstance(payload.get("content"), (Mapping, list)):
            return payload["content"]
    return payload


def _validate_response(spec: ZapiEndpoint, payload: Any) -> None:
    if not isinstance(payload, Mapping):
        raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID)
    if spec.response_shape == "list":
        data = payload.get("data")
        if not isinstance(data, list) or any(not isinstance(row, Mapping) for row in data):
            raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID)
        _validate_pagination(payload)
    elif spec.response_shape == "activity":
        data = payload.get("data")
        if not isinstance(data, Mapping) or not isinstance(data.get("Results"), list):
            raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID)
    else:
        raise SourceUnavailable(C.ZAPI_RESPONSE_INVALID)


def _validate_pagination(payload: Mapping[str, Any]) -> None:
    for key in ("start", "length", "recordsTotal", "recordsFiltered"):
        if key in payload and payload[key] is not None:
            try:
                if int(payload[key]) < 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise SourceUnavailable(f"{C.ZAPI_RESPONSE_INVALID}:pagination:{key}") from exc


def _rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    return []


def _common(
    source: str,
    endpoint: str,
    symbol: str,
    market_date: str,
    event: str,
    received: str,
    row: Mapping[str, Any],
    record_id: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "market_date": market_date,
        "event_timestamp": event or received,
        "received_at": received,
        "source": source,
        "source_record_id": f"{endpoint}:{record_id}" if record_id else endpoint,
        "raw_payload_hash": compute_payload_hash(dict(row)),
        "field_provenance": {
            "__endpoint": {
                "field_name": "__endpoint",
                "source": source,
                "value": endpoint,
                "event_timestamp": event or received,
                "confidence": 1.0,
            }
        },
    }


def _set_field_provenance(record: CanonicalRecord, endpoint: str, event: str) -> None:
    for field_name in record.domain_fields():
        value = getattr(record, field_name, None)
        if value is not None and value != "":
            record.set_provenance(field_name, record.source, value, event_timestamp=event)
    record.field_provenance["__endpoint"] = {
        "field_name": "__endpoint",
        "source": record.source,
        "value": endpoint,
        "event_timestamp": event,
        "confidence": 1.0,
    }


def _activity_status(activity_type: str, explicit: Any) -> tuple[str, bool, bool]:
    text = _text(explicit).upper()
    if text in {"SUSPEND", "SUSPENDED", "HALT"}:
        return text, False, True
    if activity_type == "suspend":
        return "SUSPENDED", False, True
    if activity_type == "relisting":
        return "RELISTING", True, False
    if activity_type == "uma":
        return "UMA", True, False
    return "UNKNOWN", False, False


def _tradable(company: Mapping[str, Any]) -> bool:
    if "EfekEmiten_Saham" in company:
        return _bool(company.get("EfekEmiten_Saham"), default=True)
    return True


def _date_param(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not _DATE_RE.fullmatch(text):
        raise SourceRequestInvalid("ZAPI date must be YYYYMMDD or YYYY-MM-DD")
    return text


def _market_date(value: Any, fallback: Any) -> str:
    candidate = value or fallback
    if candidate in (None, ""):
        return ""
    if isinstance(candidate, (datetime, date)):
        return candidate.date().isoformat() if isinstance(candidate, datetime) else candidate.isoformat()
    return _text(candidate)[:10]


def _timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=C.WIB)
    else:
        parsed = parsed.astimezone(C.WIB)
    return parsed.isoformat()


def _retry_after_seconds(value: Any) -> float | None:
    try:
        return max(0.0, float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _positive_int(name: str, value: Any, *, allow_zero: bool) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceRequestInvalid(f"ZAPI {name} must be an integer") from exc
    if result < (0 if allow_zero else 1):
        raise SourceRequestInvalid(f"ZAPI {name} is out of range")
    return result


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "y", "on"}


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(change: float | None, previous: float | None) -> float | None:
    if change is None or previous in (None, 0):
        return None
    return 100.0 * change / float(previous)
