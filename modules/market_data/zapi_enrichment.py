from __future__ import annotations

"""Non-blocking Zapi enrichment used by the production SDE pipeline.

The production data contract is intentionally narrow:

* Yahoo owns every OHLCV row and every technical calculation.
* Stockbit owns broker and foreign-flow data.
* Zapi is queried only for issuer metadata and exchange activity flags.

This service keeps those two Zapi concerns behind persistent caches.  A single
runtime may use at most five Zapi request attempts, and no request is made per
symbol.  A cache miss refreshes a whole directory/page and the three activity
categories as endpoint-level operations.  Source errors become a degraded
enrichment result; they never become a technical-pipeline failure.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from modules.data_sources.base import SourceError
from modules.data_sources.config import load_data_source_config
from modules.data_sources.zapi_idx_adapter import ZapiIdxAdapter, ZapiIdxClient, canonical_symbol
from swing_utils import atomic_csv, write_json as _durable_write_json


EventCallback = Callable[[str, dict[str, Any]], None]
ACTIVITY_TYPES = ("suspend", "uma", "relisting")
DEFAULT_METADATA_TTL_DAYS = 7
DEFAULT_MAX_REQUESTS = 5
DEFAULT_MINIMUM_HISTORICAL_CANDLES = 200


def _now() -> datetime:
    return datetime.now().astimezone()


def _emit(callback: EventCallback | None, event: str, **detail: Any) -> None:
    if callback is not None:
        callback(event, detail)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _durable_write_json(path, dict(payload))


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null"} else text


def _first(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = _text(row.get(name))
        if value:
            return value
    return ""


def _number(value: Any) -> float | None:
    try:
        if value in (None, "") or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        return pd.Timestamp(text).date().isoformat()
    except (TypeError, ValueError):
        return text[:10]


def _cache_date(payload: Mapping[str, Any]) -> date | None:
    for key in ("cache_date", "retrieved_date", "trade_date"):
        value = _date_text(payload.get(key))
        if value:
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    return None


def _cache_valid(payload: Mapping[str, Any], trade_date: date, ttl_days: int) -> bool:
    cached = _cache_date(payload)
    if cached is None or cached > trade_date:
        return False
    return (trade_date - cached).days < max(1, int(ttl_days))


def _rows(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    if isinstance(data, Mapping) and isinstance(data.get("data"), list):
        return [row for row in data["data"] if isinstance(row, Mapping)]
    return []


def _metadata_records(companies: Any, securities: Any, trade_date: date) -> dict[str, dict[str, Any]]:
    company_rows = _rows(companies)
    security_rows = _rows(securities)
    companies_by_code = {
        canonical_symbol(_first(row, "KodeEmiten", "Code", "StockCode", "Symbol")): row
        for row in company_rows
    }
    securities_by_code = {
        canonical_symbol(_first(row, "Code", "KodeEmiten", "StockCode", "Symbol")): row
        for row in security_rows
    }
    records: dict[str, dict[str, Any]] = {}
    for symbol in sorted(set(companies_by_code) | set(securities_by_code)):
        if not symbol:
            continue
        company = companies_by_code.get(symbol, {})
        security = securities_by_code.get(symbol, {})
        listing_date = _date_text(_first(security, "ListingDate", "TanggalListing") or _first(company, "ListingDate", "TanggalListing"))
        active = _first(company, "ActiveStatus", "Status") or _first(security, "ActiveStatus", "Status")
        records[symbol] = {
            "symbol": symbol,
            "name": _first(security, "Name", "NamaEmiten") or _first(company, "NamaEmiten", "Name"),
            "sector": _first(company, "Sektor", "Sector", "Sector_Name"),
            "sub_sector": _first(company, "SubSektor", "Sub_Sector", "SubSector"),
            "industry": _first(company, "Industri", "Industry") or _first(security, "Industry"),
            "sub_industry": _first(company, "SubIndustri", "SubIndustry") or _first(security, "SubIndustry"),
            "board": _first(security, "ListingBoard", "Board") or _first(company, "PapanPencatatan", "Board"),
            "listing_date": listing_date,
            "active_status": active,
            "is_active": _active_status(active, company, security),
            "is_tradable": _tradable(company, security),
            "listed_shares": _number(security.get("Shares")),
            "provider": "ZAPI_IDX",
            "cache_date": trade_date.isoformat(),
        }
    return records


def _active_status(value: str, company: Mapping[str, Any], security: Mapping[str, Any]) -> bool:
    raw = value.strip().upper()
    if raw in {"INACTIVE", "DELISTED", "FALSE", "0", "NO", "TIDAK"}:
        return False
    for row in (company, security):
        for key in ("IsActive", "Active", "Aktif", "EfekEmiten_Saham"):
            if key in row and row[key] is not None:
                text = _text(row[key]).upper()
                if text in {"FALSE", "0", "NO", "TIDAK"}:
                    return False
                if text in {"TRUE", "1", "YES", "YA"}:
                    return True
    return True


def _tradable(company: Mapping[str, Any], security: Mapping[str, Any]) -> bool:
    for row in (company, security):
        if "EfekEmiten_Saham" in row:
            return _text(row.get("EfekEmiten_Saham")).upper() not in {"FALSE", "0", "NO", "TIDAK"}
        if "IsTradable" in row:
            return _text(row.get("IsTradable")).upper() not in {"FALSE", "0", "NO", "TIDAK"}
    return True


def _activity_records(raw_by_type: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for activity_type, raw in raw_by_type.items():
        data = raw.get("data") if isinstance(raw, Mapping) else None
        rows = data.get("Results", []) if isinstance(data, Mapping) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = canonical_symbol(row.get("CompanyID") or row.get("Code") or row.get("KodeEmiten") or row.get("Symbol"))
            if not symbol:
                continue
            normalized = str(activity_type).upper()
            current = records.setdefault(symbol, {"symbol": symbol, "status": "NORMAL", "risk_flags": [], "veto": ""})
            if normalized == "SUSPEND":
                current["status"] = "SUSPENDED"
                current["is_suspended"] = True
                current["veto"] = "SUSPENDED"
            elif normalized == "UMA":
                current.setdefault("risk_flags", []).append("UMA")
            elif normalized == "RELISTING":
                current.setdefault("risk_flags", []).append("RELISTING")
            current["reason"] = _first(row, "Judul", "CompanyName", "Reason")
            current["activity_date"] = _date_text(row.get("UMADate") or row.get("Date"))
    for record in records.values():
        record["risk_flags"] = sorted(set(record.get("risk_flags", [])))
        if record.get("status") != "SUSPENDED":
            record["status"] = "NORMAL"
            record["is_suspended"] = False
    return records


def _history_count(folder: Path | None, symbol: str) -> int:
    if folder is None:
        return 0
    for path in (folder / f"{symbol}.csv", folder / f"{symbol}.JK.csv"):
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            frame = pd.read_csv(path, low_memory=False)
            date_col = next((col for col in frame.columns if str(col).strip().lower() in {"date", "datetime", "market_date"}), None)
            if date_col:
                return int(pd.to_datetime(frame[date_col], errors="coerce").notna().sum())
            return int(len(frame))
        except Exception:
            return 0
    return 0


class ZapiEnrichmentService:
    def __init__(
        self,
        *,
        config_path: str | Path = "config/data_sources.json",
        cache_root: str | Path = "data/state/zapi",
        client: ZapiIdxClient | None = None,
        event_callback: EventCallback | None = None,
        metadata_ttl_days: int = DEFAULT_METADATA_TTL_DAYS,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        minimum_historical_candles: int = DEFAULT_MINIMUM_HISTORICAL_CANDLES,
    ) -> None:
        self.config_path = Path(config_path)
        self.cache_root = Path(cache_root)
        self.event_callback = event_callback
        self.metadata_ttl_days = max(1, int(metadata_ttl_days))
        self.minimum_historical_candles = max(1, int(minimum_historical_candles))
        self.client = client
        self.config_error = ""
        if self.client is None:
            try:
                source = load_data_source_config(self.config_path).source("ZAPI_IDX")
                self.client = ZapiIdxClient.from_config(source) if source is not None else None
            except Exception as exc:
                self.config_error = f"{type(exc).__name__}: {exc}"
                self.client = None
        if self.client is not None:
            self.client.max_requests_per_process = min(
                max(1, int(max_requests or DEFAULT_MAX_REQUESTS)),
                int(getattr(self.client, "max_requests_per_process", max_requests or DEFAULT_MAX_REQUESTS) or max_requests or DEFAULT_MAX_REQUESTS),
            )
            self.client.set_event_callback(self._client_event)

    def _client_event(self, event: str, detail: dict[str, Any]) -> None:
        _emit(self.event_callback, event, **detail)

    @property
    def request_count(self) -> int:
        return int(getattr(self.client, "request_attempt_count", 0) or 0)

    @property
    def request_cap(self) -> int:
        return int(getattr(self.client, "max_requests_per_process", DEFAULT_MAX_REQUESTS) or DEFAULT_MAX_REQUESTS)

    def enrich(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        trade_date: date,
        historical_dir: str | Path | None = None,
        metadata_csv_path: str | Path | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        normalized = sorted(set(canonical_symbol(symbol) for symbol in symbols if canonical_symbol(symbol) and canonical_symbol(symbol) != "IHSG"))
        self.cache_root.mkdir(parents=True, exist_ok=True)
        metadata_path = self.cache_root / "metadata_cache.json"
        activity_path = self.cache_root / "market_activity_cache.json"
        metadata_cache = _read_json(metadata_path)
        activity_cache = _read_json(activity_path)

        metadata_records = dict(metadata_cache.get("records") or {})
        metadata_valid = _cache_valid(metadata_cache, trade_date, self.metadata_ttl_days)
        metadata_status = "HIT" if metadata_valid and all(symbol in metadata_records for symbol in normalized) else "MISS"
        metadata_error = ""
        if force or not metadata_valid or any(symbol not in metadata_records for symbol in normalized):
            reason = "FORCED" if force else "TTL_EXPIRED" if not metadata_valid else "SYMBOL_METADATA_MISSING"
            _emit(self.event_callback, "ZAPI_METADATA_REFRESH_START", reason=reason, symbol_count=len(normalized))
            fetched, metadata_error = self._fetch_metadata(trade_date)
            if fetched:
                metadata_records.update(fetched)
                metadata_cache = {
                    "cache_version": 1,
                    "cache_date": trade_date.isoformat(),
                    "retrieved_at": _now().isoformat(timespec="seconds"),
                    "records": metadata_records,
                    "record_count": len(metadata_records),
                    "status": "SUCCESS",
                }
                _write_json(metadata_path, metadata_cache)
                metadata_status = "REFRESHED"
                self._write_metadata_csv(metadata_records, metadata_csv_path)
            elif metadata_records:
                metadata_status = "STALE_FALLBACK"
                metadata_error = metadata_error or "ZAPI_METADATA_REFRESH_FAILED"
            else:
                metadata_status = "UNAVAILABLE"
        if metadata_csv_path and not Path(metadata_csv_path).exists() and metadata_records:
            self._write_metadata_csv(metadata_records, metadata_csv_path)

        activity_dates = dict(activity_cache.get("dates") or {})
        activity_day = activity_dates.get(trade_date.isoformat()) if isinstance(activity_dates, dict) else None
        activity_records = dict(activity_day.get("records") or {}) if isinstance(activity_day, Mapping) else {}
        fallback_activity_day: Mapping[str, Any] | None = None
        fallback_activity_date = ""
        if not isinstance(activity_day, Mapping) and isinstance(activity_dates, dict):
            previous_dates = sorted(
                date_text for date_text, item in activity_dates.items()
                if isinstance(item, Mapping) and date_text <= trade_date.isoformat()
            )
            if previous_dates:
                fallback_activity_date = previous_dates[-1]
                fallback_activity_day = activity_dates.get(fallback_activity_date)
        activity_status = "HIT" if isinstance(activity_day, Mapping) else "MISS"
        activity_error = ""
        activity_cache_date = trade_date.isoformat() if isinstance(activity_day, Mapping) else ""
        if force or not isinstance(activity_day, Mapping):
            _emit(self.event_callback, "ZAPI_MARKET_ACTIVITY_REFRESH_START", trade_date=trade_date.isoformat())
            fetched_activity, activity_error = self._fetch_activity(trade_date)
            if fetched_activity or not activity_error:
                activity_records = fetched_activity
                activity_dates[trade_date.isoformat()] = {
                    "cache_date": trade_date.isoformat(),
                    "retrieved_at": _now().isoformat(timespec="seconds"),
                    "records": activity_records,
                    "status": "SUCCESS" if not activity_error else "PARTIAL",
                }
                _write_json(activity_path, {"cache_version": 1, "dates": activity_dates})
                activity_status = "REFRESHED" if not activity_error else "PARTIAL"
            elif activity_day:
                activity_status = "STALE_FALLBACK"
                activity_error = activity_error or "ZAPI_MARKET_ACTIVITY_REFRESH_FAILED"
            elif fallback_activity_day:
                activity_records = dict(fallback_activity_day.get("records") or {})
                activity_status = "STALE_FALLBACK"
                activity_cache_date = fallback_activity_date
                activity_error = activity_error or "ZAPI_MARKET_ACTIVITY_REFRESH_FAILED"
            else:
                activity_status = "UNAVAILABLE"

        history_folder = Path(historical_dir) if historical_dir else None
        by_symbol: dict[str, dict[str, Any]] = {}
        for symbol in normalized:
            metadata = dict(metadata_records.get(symbol) or {})
            activity = dict(activity_records.get(symbol) or {})
            count = _history_count(history_folder, symbol)
            risk_flags = sorted(set(activity.get("risk_flags") or []))
            veto = str(activity.get("veto") or "")
            if "RELISTING" in risk_flags and count < self.minimum_historical_candles:
                veto = "RELISTING_HISTORY_INSUFFICIENT"
            by_symbol[symbol] = {
                "symbol": symbol,
                "metadata": metadata,
                "status": activity.get("status", "NORMAL"),
                "is_suspended": bool(activity.get("is_suspended", False)),
                "risk_flags": risk_flags,
                "veto": veto,
                "activity_reason": activity.get("reason", ""),
                "activity_date": activity.get("activity_date", ""),
                "history_candle_count": count,
                "minimum_historical_candles": self.minimum_historical_candles,
            }

        suspended = sorted(symbol for symbol, row in by_symbol.items() if row.get("status") == "SUSPENDED")
        uma = sorted(symbol for symbol, row in by_symbol.items() if "UMA" in row.get("risk_flags", []))
        relisting = sorted(symbol for symbol, row in by_symbol.items() if "RELISTING" in row.get("risk_flags", []))
        degraded = bool(metadata_error or activity_error or metadata_status in {"UNAVAILABLE", "STALE_FALLBACK"} or activity_status in {"UNAVAILABLE", "STALE_FALLBACK", "PARTIAL"})
        reason_parts = [part for part in (metadata_error, activity_error, self.config_error) if part]
        payload = {
            "status": "DEGRADED" if degraded else "SUCCESS",
            "source": "ZAPI_IDX",
            "degraded": degraded,
            "degraded_reason": "; ".join(reason_parts),
            "trade_date": trade_date.isoformat(),
            "request_count": self.request_count,
            "request_cap": self.request_cap,
            "request_cap_reached": bool(getattr(self.client, "request_cap_reached", False)),
            "metadata_cache_status": metadata_status,
            "metadata_cache_date": (_cache_date(metadata_cache) or date.min).isoformat() if metadata_cache else "",
            "metadata_cache_path": str(metadata_path),
            "metadata_record_count": len(metadata_records),
            "market_activity_cache_status": activity_status,
            "market_activity_cache_date": activity_cache_date or (trade_date.isoformat() if activity_status in {"REFRESHED", "PARTIAL"} else ""),
            "market_activity_cache_path": str(activity_path),
            "suspended_symbols": suspended,
            "uma_symbols": uma,
            "relisting_symbols": relisting,
            "suspended_count": len(suspended),
            "uma_count": len(uma),
            "relisting_count": len(relisting),
            "symbols": by_symbol,
        }
        runtime_path = self.cache_root / f"enrichment_{trade_date.isoformat()}.json"
        # Persist the artifact path inside the artifact itself.  The in-memory
        # result is also consumed by the current stage, while a later stage or
        # a restarted process may only have the JSON file to establish lineage.
        payload["path"] = str(runtime_path)
        _write_json(runtime_path, payload)
        _write_json(self.cache_root / "enrichment_latest.json", payload)
        _emit(self.event_callback, "ZAPI_ENRICHMENT_COMPLETE", **{key: value for key, value in payload.items() if key != "symbols"})
        return payload

    def _fetch_metadata(self, trade_date: date) -> tuple[dict[str, dict[str, Any]], str]:
        if self.client is None:
            return {}, self.config_error or "ZAPI_NOT_CONFIGURED"
        if not self.client.is_configured() and not bool(getattr(self.client, "_explicit_mock", False)):
            return {}, "ZAPI_NOT_CONFIGURED"
        raw: dict[str, Any] = {}
        errors: list[str] = []
        for endpoint in ("companies", "securities"):
            try:
                result = self.client.fetch_raw(
                    "SymbolMetadata",
                    "",
                    length=1000,
                    start=0,
                    metadata_endpoint=endpoint,
                )
                raw[endpoint] = result.get(endpoint, {}) if isinstance(result, Mapping) else {}
            except SourceError as exc:
                errors.append(f"{endpoint}:{type(exc).__name__}:{exc}")
                _emit(self.event_callback, "ZAPI_METADATA_ENDPOINT_FAILED", endpoint=endpoint, error=str(exc))
            except Exception as exc:
                errors.append(f"{endpoint}:{type(exc).__name__}:{exc}")
        records = _metadata_records(raw.get("companies", {}), raw.get("securities", {}), trade_date)
        return records, "; ".join(errors)

    def _fetch_activity(self, trade_date: date) -> tuple[dict[str, dict[str, Any]], str]:
        if self.client is None:
            return {}, self.config_error or "ZAPI_NOT_CONFIGURED"
        if not self.client.is_configured() and not bool(getattr(self.client, "_explicit_mock", False)):
            return {}, "ZAPI_NOT_CONFIGURED"
        raw_by_type: dict[str, Any] = {}
        errors: list[str] = []
        for activity_type in ACTIVITY_TYPES:
            try:
                raw_by_type[activity_type] = self.client.fetch_raw(
                    "TradingStatus",
                    "",
                    activity_type=activity_type,
                    market_date=trade_date.isoformat(),
                )
            except SourceError as exc:
                errors.append(f"{activity_type}:{type(exc).__name__}:{exc}")
                _emit(self.event_callback, "ZAPI_ACTIVITY_ENDPOINT_FAILED", activity_type=activity_type, error=str(exc))
            except Exception as exc:
                errors.append(f"{activity_type}:{type(exc).__name__}:{exc}")
        return _activity_records(raw_by_type), "; ".join(errors)

    @staticmethod
    def _write_metadata_csv(records: Mapping[str, Mapping[str, Any]], output_path: str | Path | None) -> None:
        if not output_path or not records:
            return
        output = Path(output_path)
        rows = []
        for symbol, record in sorted(records.items()):
            rows.append({
                "Symbol": symbol,
                "Name": record.get("name", ""),
                "Sector": record.get("sector", ""),
                "SubSector": record.get("sub_sector", ""),
                "Industry": record.get("industry", ""),
                "SubIndustry": record.get("sub_industry", ""),
                "Board": record.get("board", ""),
                "ListingDate": record.get("listing_date", ""),
                "ActiveStatus": record.get("active_status", ""),
                "IsActive": record.get("is_active", True),
                "Provider": "ZAPI_IDX",
                "Retrieved_Date": record.get("cache_date", ""),
            })
        atomic_csv(pd.DataFrame(rows), output, encoding="utf-8-sig")


def load_cached_enrichment(path: str | Path, trade_date: date | None = None) -> dict[str, Any]:
    """Load the persistent enrichment artifact without making a request."""
    payload = _read_json(Path(path))
    if trade_date is not None and payload.get("trade_date") not in {None, "", trade_date.isoformat()}:
        return {}
    return payload


__all__ = [
    "ACTIVITY_TYPES",
    "DEFAULT_MAX_REQUESTS",
    "DEFAULT_METADATA_TTL_DAYS",
    "DEFAULT_MINIMUM_HISTORICAL_CANDLES",
    "ZapiEnrichmentService",
    "load_cached_enrichment",
]
