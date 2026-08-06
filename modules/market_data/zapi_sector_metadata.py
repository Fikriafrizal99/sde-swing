from __future__ import annotations

"""Optional ZAPI-backed sector metadata cache for Market Outlook.

Sector rotation is presentation-only.  This helper refreshes the local
``sector_metadata.csv`` cache from the documented ``/companies`` response
through :class:`ZapiIdxClient`; it never feeds technical scores or decisions.
Missing credentials or an unavailable endpoint leave the existing cache
untouched so Market Outlook can retain its explicit fail-closed warning.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from modules.data_sources.config import load_data_source_config
from modules.data_sources.zapi_idx_adapter import ZapiIdxClient, canonical_symbol


EventCallback = Callable[[str, dict[str, Any]], None]


def _emit(callback: EventCallback | None, event: str, **detail: Any) -> None:
    if callback is not None:
        callback(event, detail)


def _cached_for_trade_date(path: Path, trade_date: date) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        cached_date = None
        try:
            cached = pd.read_csv(path, nrows=1)
            if "Retrieved_Date" in cached.columns and not cached.empty:
                cached_date = pd.to_datetime(cached.iloc[0]["Retrieved_Date"], errors="coerce").date()
        except Exception:
            cached_date = None
        cached_date = cached_date or datetime.fromtimestamp(path.stat().st_mtime).date()
        age = (trade_date - cached_date).days
        return 0 <= age < 7
    except (OSError, ValueError, TypeError):
        return False


def _company_page(raw: Any) -> tuple[list[Mapping[str, Any]], int]:
    if not isinstance(raw, Mapping):
        return [], 0
    companies = raw.get("companies")
    if not isinstance(companies, Mapping):
        return [], 0
    rows = companies.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        return [], 0
    total = companies.get("recordsTotal", len(rows))
    try:
        records_total = max(int(total), 0)
    except (TypeError, ValueError):
        records_total = len(rows)
    return rows, records_total


def _first_text(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, "") and str(value).strip().lower() not in {"nan", "none"}:
            return str(value).strip()
    return ""


def refresh_sector_metadata(
    output_path: str | Path,
    *,
    config_path: str | Path,
    trade_date: date,
    client: ZapiIdxClient | None = None,
    page_size: int = 1000,
    event_callback: EventCallback | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Refresh the local sector cache from paginated ZAPI companies data."""

    output = Path(output_path)
    page_size = max(1, int(page_size or 1000))
    if not force and _cached_for_trade_date(output, trade_date):
        _emit(event_callback, "ZAPI_SECTOR_METADATA_CACHED", path=str(output), trade_date=trade_date.isoformat())
        return {
            "status": "CACHED",
            "source_mode": "CACHE",
            "path": str(output),
            "records_written": 0,
            "request_count": 0,
        }

    try:
        source = load_data_source_config(config_path).source("ZAPI_IDX")
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        _emit(event_callback, "ZAPI_SECTOR_METADATA_FAILED", reason=reason, request_count=0)
        return {"status": "FAILED", "source_mode": "UNAVAILABLE", "reason": reason, "request_count": 0}

    if source is None or not source.enabled:
        reason = "ZAPI_SOURCE_DISABLED_OR_CONFIG_NOT_FOUND"
        _emit(event_callback, "ZAPI_SECTOR_METADATA_SKIPPED", reason=reason, request_count=0)
        return {"status": "NOT_CONFIGURED", "source_mode": "UNAVAILABLE", "reason": reason, "request_count": 0}

    zapi_client = client or ZapiIdxClient.from_config(source)
    zapi_client.max_requests_per_process = min(
        5,
        int(getattr(zapi_client, "max_requests_per_process", 5) or 5),
    )
    if client is None and not zapi_client.is_configured():
        reason = "ZAPI_MISSING_CREDENTIAL"
        _emit(event_callback, "ZAPI_SECTOR_METADATA_SKIPPED", reason=reason, request_count=0)
        return {"status": "NOT_CONFIGURED", "source_mode": "UNAVAILABLE", "reason": reason, "request_count": 0}

    _emit(
        event_callback,
        "ZAPI_SECTOR_METADATA_START",
        page_size=page_size,
        trade_date=trade_date.isoformat(),
    )
    rows_by_symbol: dict[str, dict[str, str]] = {}
    request_count_before = zapi_client.request_attempt_count
    records_total = 0
    page_start = 0
    pages = 0
    try:
        max_pages = 1000
        while pages < max_pages:
            raw = zapi_client.fetch_raw(
                "SymbolMetadata",
                "",
                length=page_size,
                start=page_start,
                companies_only=True,
            )
            page_rows, page_total = _company_page(raw)
            records_total = max(records_total, page_total)
            if not page_rows:
                if page_start < records_total:
                    raise ValueError("ZAPI_COMPANIES_EMPTY_PAGE_BEFORE_TOTAL")
                break
            for row in page_rows:
                symbol = canonical_symbol(_first_text(row, "KodeEmiten", "Code", "StockCode", "Symbol"))
                sector = _first_text(row, "Sektor", "Sector", "Sector_Name", "SubSektor", "Sub_Sector")
                sub_sector = _first_text(row, "SubSektor", "Sub_Sector", "SubSector", "Industry")
                if symbol and sector:
                    rows_by_symbol[symbol] = {
                        "Symbol": symbol,
                        "Sector": sector,
                        "SubSector": sub_sector,
                    }
            pages += 1
            page_count = len(page_rows)
            page_start += page_count
            _emit(
                event_callback,
                "ZAPI_SECTOR_METADATA_PROGRESS",
                page_start=page_start,
                page_size=page_size,
                records_indexed=len(rows_by_symbol),
                records_total=records_total,
                request_count=zapi_client.request_attempt_count - request_count_before,
            )
            if page_count < page_size or (records_total and page_start >= records_total):
                break
        if pages >= max_pages:
            raise ValueError("ZAPI_COMPANIES_PAGINATION_LIMIT")
        if not rows_by_symbol:
            raise ValueError("ZAPI_SECTOR_METADATA_EMPTY")

        frame = pd.DataFrame(sorted(rows_by_symbol.values(), key=lambda item: item["Symbol"]))
        frame["Provider"] = "ZAPI_IDX"
        frame["Retrieved_Date"] = trade_date.isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        temporary.replace(output)
        request_count = zapi_client.request_attempt_count - request_count_before
        result = {
            "status": "SUCCESS",
            "source_mode": "LIVE",
            "path": str(output),
            "records_total": records_total,
            "records_written": len(frame),
            "pages": pages,
            "page_size": page_size,
            "request_count": request_count,
        }
        _emit(event_callback, "ZAPI_SECTOR_METADATA_COMPLETE", **result)
        return result
    except Exception as exc:
        request_count = zapi_client.request_attempt_count - request_count_before
        reason = f"{type(exc).__name__}: {exc}"
        _emit(event_callback, "ZAPI_SECTOR_METADATA_FAILED", reason=reason, request_count=request_count)
        return {
            "status": "FAILED",
            "source_mode": "LIVE",
            "reason": reason,
            "path": str(output),
            "records_total": records_total,
            "records_written": 0,
            "request_count": request_count,
        }
