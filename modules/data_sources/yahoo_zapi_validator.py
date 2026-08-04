from __future__ import annotations

"""Yahoo/ZAPI latest-candle validation and immutable source lineage.

Yahoo remains the source of truth for the historical series.  This module
validates the latest closed candle and records ZAPI metadata; it never inserts
or overwrites a Yahoo candle and never changes an engine score.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from modules.data_sources.base import SourceNotConfigured, SourceUnavailable
from modules.data_sources.config import load_data_source_config
from modules.data_sources.zapi_idx_adapter import (
    ZapiIdxAdapter,
    ZapiIdxClient,
    canonical_symbol,
)
from modules.market_calendar.idx_calendar import previous_idx_trading_day


PROBLEM_STATUSES = {
    "STALE_ZAPI", "STALE_YAHOO", "DATE_MISMATCH", "PRICE_MISMATCH",
    "MISSING_ZAPI", "MISSING_YAHOO", "INVALID_SCHEMA",
}


def _latest_yahoo_bar(folder: Path, symbol: str) -> dict[str, Any] | None:
    for path in (folder / f"{symbol}.csv", folder / f"{symbol}.JK.csv"):
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        date_col = next(
            (c for c in frame.columns if str(c).strip().lower() in {"date", "datetime", "market_date"}),
            None,
        )
        if not date_col:
            continue
        parsed = pd.to_datetime(frame[date_col], errors="coerce")
        valid = frame.loc[parsed.notna()].copy()
        if valid.empty:
            continue
        valid["__parsed_date"] = parsed.loc[parsed.notna()]
        row = valid.sort_values("__parsed_date").iloc[-1]

        def num(*names: str) -> float | None:
            for name in names:
                if name in row.index:
                    try:
                        return None if pd.isna(row[name]) else float(row[name])
                    except (TypeError, ValueError):
                        return None
            return None

        stamp = pd.Timestamp(row["__parsed_date"])
        return {
            "path": str(path),
            "trade_date": stamp.date().isoformat(),
            "timestamp": stamp.isoformat(),
            "open": num("Open", "open"),
            "high": num("High", "high"),
            "low": num("Low", "low"),
            "close": num("Close", "close"),
            "volume": num("Volume", "volume"),
        }
    return None


def _pct_diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return abs(left - right) / max(abs(right), 1e-9)


def _days_behind(actual: str, expected: str) -> int | None:
    try:
        return (date.fromisoformat(expected) - date.fromisoformat(actual)).days
    except (TypeError, ValueError):
        return None


def _market_day(value: str) -> date:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return date.fromisoformat(text[:10])


def _classify(
    *,
    yahoo: dict[str, Any] | None,
    zapi: Any | None,
    expected_date: str,
    price_diffs: list[float],
    price_tolerance: float,
    error_status: str,
) -> str:
    if yahoo is None:
        return "MISSING_YAHOO"
    if error_status == "INVALID_SCHEMA":
        return "INVALID_SCHEMA"
    if zapi is None:
        return "MISSING_ZAPI"
    yahoo_date = str(yahoo.get("trade_date") or "")
    zapi_date = str(getattr(zapi, "market_date", "") or "")
    if yahoo_date != zapi_date:
        if yahoo_date == expected_date:
            return "STALE_ZAPI" if (_days_behind(zapi_date, expected_date) or 0) > 0 else "DATE_MISMATCH"
        if zapi_date == expected_date:
            return "STALE_YAHOO" if (_days_behind(yahoo_date, expected_date) or 0) > 0 else "DATE_MISMATCH"
        return "DATE_MISMATCH"
    if not price_diffs or any(value > price_tolerance for value in price_diffs):
        return "PRICE_MISMATCH"
    if all(value <= 1e-12 for value in price_diffs):
        return "MATCH"
    return "MATCH_WITH_TOLERANCE"


def validate_yahoo_against_zapi(
    *,
    historical_dir: str | Path,
    symbols: list[str],
    market_date: str,
    output_dir: str | Path,
    config_path: str | Path = "config/data_sources.json",
    price_tolerance_pct: float = 0.005,
    volume_tolerance_pct: float = 0.20,
    maximum_stale_days: int = 1,
    minimum_coverage_ratio: float = 0.90,
    blocking: bool = False,
    max_symbols: int = 0,
    run_id: str = "",
    client: ZapiIdxClient | None = None,
    event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    market_holidays: Iterable[Any] = (),
    special_trading_days: Iterable[Any] = (),
    max_date_fallback_sessions: int = 10,
    bulk_page_size: int = 100,
    block_on_price_mismatch: bool = True,
) -> dict[str, Any]:
    """Validate Yahoo bars against live ZAPI and persist per-run lineage."""
    folder = Path(historical_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    normalized = list(dict.fromkeys(canonical_symbol(item) for item in symbols if canonical_symbol(item)))
    normalized = [item for item in normalized if item != "IHSG"]
    if max_symbols > 0:
        normalized = normalized[:max_symbols]
    run_id = run_id or f"ZAPI-{market_date}-{datetime.now().strftime('%H%M%S')}"

    def emit(event: str, **detail: Any) -> None:
        if event_callback is not None:
            event_callback(event, detail)

    cfg = load_data_source_config(config_path)
    source_cfg = cfg.sources.get("ZAPI_IDX")
    if source_cfg is None:
        emit("ZAPI_CREDENTIAL_STATUS", status="ZAPI_SOURCE_CONFIG_NOT_FOUND")
        emit("ZAPI_SMOKE_TEST_START", symbol=normalized[0] if normalized else "")
        emit("ZAPI_SMOKE_TEST_FAILED", reason="ZAPI_SOURCE_CONFIG_NOT_FOUND", request_performed=False)
        result = _write_skipped(out, run_id, market_date, normalized, "ZAPI_SOURCE_CONFIG_NOT_FOUND", status="ZAPI_MISSING_CREDENTIAL", blocking=blocking)
        emit("ZAPI_RECONCILIATION_COMPLETE", status=result["status"], request_count=0)
        return result
    if not source_cfg.enabled:
        emit("ZAPI_CREDENTIAL_STATUS", status="ZAPI_DISABLED")
        emit("ZAPI_SMOKE_TEST_START", symbol=normalized[0] if normalized else "")
        emit("ZAPI_SMOKE_TEST_FAILED", reason="ZAPI_DISABLED", request_performed=False)
        result = _write_skipped(out, run_id, market_date, normalized, "ZAPI_DISABLED", status="ZAPI_DISABLED", blocking=blocking)
        emit("ZAPI_RECONCILIATION_COMPLETE", status=result["status"], request_count=0)
        return result
    zapi_client = client or ZapiIdxClient.from_config(source_cfg)
    if not zapi_client.is_configured() and client is None:
        emit("ZAPI_CREDENTIAL_STATUS", status="ZAPI_MISSING_CREDENTIAL")
        emit("ZAPI_SMOKE_TEST_START", symbol=normalized[0] if normalized else "")
        emit("ZAPI_SMOKE_TEST_FAILED", reason="ZAPI_MISSING_CREDENTIAL", request_performed=False)
        result = _write_skipped(out, run_id, market_date, normalized, "ZAPI_MISSING_CREDENTIAL", status="ZAPI_MISSING_CREDENTIAL", blocking=blocking)
        emit("ZAPI_RECONCILIATION_COMPLETE", status=result["status"], request_count=0)
        return result

    zapi_client.set_event_callback(event_callback)
    emit("ZAPI_CREDENTIAL_STATUS", status="CONFIGURED")

    adapter = ZapiIdxAdapter(zapi_client)
    rows: list[dict[str, Any]] = []
    endpoint_counts: dict[str, int] = {"/stock-summary": 0}
    success_count = 0
    failure_count = 0

    requested_session = _market_day(market_date)
    requested_query_date = requested_session.strftime("%Y%m%d")
    resolved_session: date | None = None
    resolved_query_date = ""
    fallback_reason = ""
    smoke_symbol = ""
    smoke_raw: Any | None = None
    smoke_raw_by_symbol: dict[str, Any] = {}
    records_total = 0
    emit("ZAPI_SMOKE_TEST_START", requested_date=requested_query_date)
    try:
        candidate = requested_session
        attempts = max(1, int(max_date_fallback_sessions) + 1)
        for probe_index in range(attempts):
            candidate_query = candidate.strftime("%Y%m%d")
            candidate_raw = zapi_client.fetch_raw(
                "DailyBar", "", market_date=candidate.isoformat(), date=candidate_query,
                length=1, start=0,
            )
            candidate_rows = candidate_raw.get("data") if isinstance(candidate_raw, Mapping) else None
            if not isinstance(candidate_rows, list):
                raise SourceUnavailable("ZAPI_RESPONSE_INVALID")
            if candidate_rows:
                smoke_raw = candidate_raw
                resolved_session = candidate
                resolved_query_date = candidate_query
                records_total = int(candidate_raw.get("recordsTotal", 0))
                break
            if probe_index == 0:
                fallback_reason = "CURRENT_DATASET_EMPTY"
            candidate = previous_idx_trading_day(
                candidate,
                holidays=market_holidays,
                special_trading_days=special_trading_days,
            )
        if smoke_raw is None or resolved_session is None:
            raise SourceUnavailable("ZAPI_EMPTY_DATASET")

        emit(
            "ZAPI_DATE_RESOLUTION",
            requested_date=requested_query_date,
            resolved_source_date=resolved_query_date,
            fallback_reason=fallback_reason or "NONE",
        )
        first_row = smoke_raw["data"][0]
        required = ("StockCode", "Date", "Close", "Volume")
        missing = [
            field for field in required
            if field not in first_row or first_row[field] is None or str(first_row[field]).strip() == ""
        ]
        if missing:
            raise SourceUnavailable("ZAPI_RESPONSE_INVALID:missing:" + ",".join(missing))
        smoke_symbol = canonical_symbol(first_row["StockCode"])
        smoke_mapped = adapter.to_canonical(
            "DailyBar", smoke_raw, symbol=smoke_symbol,
            market_date=resolved_session.isoformat(),
        )
        if not smoke_mapped:
            raise SourceUnavailable("ZAPI_SMOKE_EMPTY_OR_INVALID_SCHEMA")
        smoke_raw_by_symbol[smoke_symbol] = smoke_raw
        endpoint_counts["/stock-summary"] = zapi_client.request_attempt_count
        emit(
            "ZAPI_SMOKE_TEST_SUCCESS",
            symbol=smoke_symbol,
            recordsTotal=records_total,
            resolved_source_date=resolved_query_date,
            request_attempts=zapi_client.request_attempt_count,
        )
    except Exception as exc:
        endpoint_counts["/stock-summary"] = zapi_client.request_attempt_count
        reason = f"{type(exc).__name__}: {exc}"
        emit(
            "ZAPI_SMOKE_TEST_FAILED",
            symbol=smoke_symbol,
            reason=reason,
            request_performed=zapi_client.request_attempt_count > 0,
            request_attempts=zapi_client.request_attempt_count,
        )
        result = _write_skipped(
            out,
            run_id,
            market_date,
            normalized,
            f"ZAPI_SMOKE_TEST_FAILED:{reason}",
            status="FAILED_BLOCKING" if blocking else "SUCCESS_WITH_WARNING",
            blocking=blocking,
            request_count=zapi_client.request_attempt_count,
            failure_count=zapi_client.request_attempt_count,
        )
        emit(
            "ZAPI_RECONCILIATION_COMPLETE",
            status=result["status"],
            request_count=result["request_count"],
            failure_count=result["failure_count"],
        )
        return result

    resolved_iso_date = resolved_session.isoformat()
    bulk_page_size = max(1, int(bulk_page_size or 100))
    emit(
        "ZAPI_BATCH_START",
        symbol_count=len(normalized),
        start_index=1,
        resolved_source_date=resolved_query_date,
        mode="BULK_PAGINATED",
        page_size=bulk_page_size,
    )

    # The stock-summary endpoint exposes the complete dataset through
    # ``length``/``start`` pagination.  Fetching that dataset once per page is
    # materially faster than issuing one HTTP request for every symbol.  The
    # smoke request remains separate and unfiltered so authentication/schema
    # failures are detected before this batch begins.
    bulk_records: dict[str, Any] = {}
    bulk_available = False
    emit(
        "ZAPI_BULK_START",
        page_size=bulk_page_size,
        records_total=records_total,
        resolved_source_date=resolved_query_date,
    )
    try:
        page_start = 0
        pages = 0
        max_pages = max(1, (max(records_total, 1) + bulk_page_size - 1) // bulk_page_size + 1)
        while page_start < max(records_total, 1) and pages < max_pages:
            raw_page = zapi_client.fetch_raw(
                "DailyBar", "", market_date=resolved_iso_date,
                date=resolved_query_date, length=bulk_page_size, start=page_start,
            )
            page_rows = raw_page.get("data") if isinstance(raw_page, Mapping) else None
            if not isinstance(page_rows, list):
                raise SourceUnavailable("ZAPI_RESPONSE_INVALID")
            if not page_rows:
                break
            mapped_page = adapter.to_canonical(
                "DailyBar", raw_page, market_date=resolved_iso_date,
            )
            for record in mapped_page:
                symbol = canonical_symbol(getattr(record, "symbol", ""))
                if symbol and symbol in normalized:
                    bulk_records.setdefault(symbol, record)
            pages += 1
            page_count = len(page_rows)
            page_start += page_count
            endpoint_counts["/stock-summary"] = zapi_client.request_attempt_count
            emit(
                "ZAPI_BULK_PROGRESS",
                page_start=page_start,
                page_size=bulk_page_size,
                records_indexed=len(bulk_records),
                records_total=records_total,
                request_count=zapi_client.request_attempt_count,
            )
            if page_count < bulk_page_size or page_start >= records_total:
                break
        if not bulk_records and normalized:
            raise SourceUnavailable("ZAPI_BULK_NO_REQUESTED_SYMBOLS")
        bulk_available = True
    except Exception as exc:
        endpoint_counts["/stock-summary"] = zapi_client.request_attempt_count
        emit(
            "ZAPI_BULK_FAILED",
            reason=f"{type(exc).__name__}: {exc}",
            request_count=zapi_client.request_attempt_count,
        )
        emit(
            "ZAPI_BATCH_FALLBACK_PER_SYMBOL",
            reason="BULK_UNAVAILABLE",
            remaining_symbols=len(normalized),
        )

    for index, symbol in enumerate(normalized, start=1):
        yahoo = _latest_yahoo_bar(folder, symbol)
        zapi_record = None
        error = ""
        error_status = ""
        try:
            if bulk_available:
                zapi_record = bulk_records.get(symbol)
                if zapi_record is None:
                    error_status = "MISSING_ZAPI"
                    failure_count += 1
                else:
                    success_count += 1
            else:
                raw = smoke_raw_by_symbol.get(symbol) or zapi_client.fetch_raw(
                    "DailyBar", symbol, market_date=resolved_iso_date,
                    date=resolved_query_date, length=10, start=0,
                )
                endpoint_counts["/stock-summary"] = zapi_client.request_attempt_count
                mapped = adapter.to_canonical(
                    "DailyBar", raw, symbol=symbol, market_date=resolved_iso_date
                )
                candidates = [item for item in mapped if canonical_symbol(item.symbol) == symbol]
                zapi_record = max(candidates or mapped, key=lambda item: item.market_date, default=None)
                if zapi_record is None:
                    error_status = "INVALID_SCHEMA" if mapped else "MISSING_ZAPI"
                    failure_count += 1
                else:
                    success_count += 1
        except SourceNotConfigured as exc:
            error, error_status = str(exc), "MISSING_ZAPI"
            failure_count += 1
        except SourceUnavailable as exc:
            error = f"{type(exc).__name__}: {exc}"
            error_status = "INVALID_SCHEMA" if "INVALID" in str(exc).upper() else "MISSING_ZAPI"
            failure_count += 1
        except Exception as exc:
            error, error_status = f"{type(exc).__name__}: {exc}", "MISSING_ZAPI"
            failure_count += 1

        diffs = {
            name: _pct_diff(yahoo.get(name) if yahoo else None, getattr(zapi_record, name, None) if zapi_record else None)
            for name in ("open", "high", "low", "close", "volume")
        }
        price_diffs = [diffs[name] for name in ("open", "high", "low", "close") if diffs[name] is not None]
        status = _classify(
            yahoo=yahoo,
            zapi=zapi_record,
            expected_date=resolved_iso_date,
            price_diffs=price_diffs,
            price_tolerance=price_tolerance_pct,
            error_status=error_status,
        )
        zapi_date = str(getattr(zapi_record, "market_date", "") or "")
        stale_days = _days_behind(zapi_date, resolved_iso_date) if zapi_date else None
        if status == "STALE_ZAPI" and stale_days is not None and stale_days <= maximum_stale_days:
            # Still stale, but the explicit age lets callers decide severity.
            error = error or f"ZAPI candle is {stale_days} trading/calendar day(s) behind"
        rows.append({
            "run_id": run_id,
            "symbol": symbol,
            "requested_date": requested_query_date,
            "resolved_source_date": resolved_query_date,
            "yahoo_trade_date": yahoo.get("trade_date") if yahoo else None,
            "zapi_trade_date": zapi_date or None,
            "yahoo_timestamp": yahoo.get("timestamp") if yahoo else None,
            "zapi_timestamp": getattr(zapi_record, "event_timestamp", None) if zapi_record else None,
            "yahoo_open": yahoo.get("open") if yahoo else None,
            "yahoo_high": yahoo.get("high") if yahoo else None,
            "yahoo_low": yahoo.get("low") if yahoo else None,
            "yahoo_close": yahoo.get("close") if yahoo else None,
            "yahoo_volume": yahoo.get("volume") if yahoo else None,
            "zapi_open": getattr(zapi_record, "open", None) if zapi_record else None,
            "zapi_high": getattr(zapi_record, "high", None) if zapi_record else None,
            "zapi_low": getattr(zapi_record, "low", None) if zapi_record else None,
            "zapi_close": getattr(zapi_record, "close", None) if zapi_record else None,
            "zapi_volume": getattr(zapi_record, "volume", None) if zapi_record else None,
            "open_difference_pct": diffs["open"],
            "high_difference_pct": diffs["high"],
            "low_difference_pct": diffs["low"],
            "close_difference_pct": diffs["close"],
            "volume_difference_pct": diffs["volume"],
            "freshness_days": stale_days,
            "trading_status": "NOT_FETCHED",
            "status": status,
            "blocking": bool(
                blocking
                and status in PROBLEM_STATUSES
                and (status != "PRICE_MISMATCH" or block_on_price_mismatch)
            ),
            "selected_source": "YAHOO",
            "enrichment_source": "ZAPI_IDX" if zapi_record else "NONE",
            "endpoint": getattr(zapi_record, "source_record_id", "").split(":", 1)[0] if zapi_record else "/stock-summary",
            "error": error,
        })
        if index == 1 or index % 10 == 0 or index == len(normalized):
            emit(
                "ZAPI_BATCH_PROGRESS",
                processed=index,
                total=len(normalized),
                success_count=success_count,
                failure_count=failure_count,
                request_count=zapi_client.request_attempt_count,
                mode="BULK_PAGINATED" if bulk_available else "PER_SYMBOL_FALLBACK",
            )

    validated = sum(row["status"] in {"MATCH", "MATCH_WITH_TOLERANCE", "PRICE_MISMATCH"} for row in rows)
    coverage = validated / len(rows) if rows else 0.0
    counts = {status: sum(row["status"] == status for row in rows) for status in sorted(PROBLEM_STATUSES | {"MATCH", "MATCH_WITH_TOLERANCE"})}
    blocking_failures = sum(bool(row["blocking"]) for row in rows)
    coverage_failed = coverage < minimum_coverage_ratio
    status = "SUCCESS"
    if blocking and (blocking_failures or coverage_failed):
        status = "FAILED_BLOCKING"
    elif any(counts[item] for item in PROBLEM_STATUSES) or coverage_failed:
        status = "SUCCESS_WITH_WARNING"

    summary = {
        "run_id": run_id,
        "trade_date": market_date,
        "requested_date": requested_query_date,
        "resolved_source_date": resolved_query_date,
        "date_fallback_reason": fallback_reason or "NONE",
        "records_total": records_total,
        "batch_mode": "BULK_PAGINATED" if bulk_available else "PER_SYMBOL_FALLBACK",
        "bulk_page_size": bulk_page_size,
        "bulk_symbols_indexed": len(bulk_records),
        "block_on_price_mismatch": block_on_price_mismatch,
        "config_version": cfg.config_version,
        "status": status,
        "reason": "MINIMUM_COVERAGE_NOT_MET" if coverage_failed else "",
        "provider": "ZAPI_IDX",
        "execution_source": "YAHOO",
        "validation_source": "ZAPI_IDX",
        "source_mode": "LIVE",
        "endpoint_logical_names": list(endpoint_counts),
        "endpoint_request_counts": endpoint_counts,
        "request_count": zapi_client.request_attempt_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "symbols_requested": len(rows),
        "symbols_successful": success_count,
        "validated": validated,
        "coverage_ratio": coverage,
        "minimum_coverage_ratio": minimum_coverage_ratio,
        "freshness_limit_days": maximum_stale_days,
        "price_tolerance_pct": price_tolerance_pct,
        "volume_tolerance_pct": volume_tolerance_pct,
        "blocking": blocking,
        "blocking_failures": blocking_failures + int(blocking and coverage_failed and not blocking_failures),
        "reconciliation_counts": counts,
        "selected_source": "YAHOO",
        "enrichment_source": "ZAPI_IDX",
        "warnings": sorted({row["status"] for row in rows if row["status"] in PROBLEM_STATUSES}),
        "input_paths": [str(folder), str(Path(config_path))],
        "rows": rows,
    }
    result = _write_outputs(out, run_id, market_date, rows, summary)
    emit(
        "ZAPI_RECONCILIATION_COMPLETE",
        status=result["status"],
        request_count=result["request_count"],
        success_count=result["success_count"],
        failure_count=result["failure_count"],
        coverage_ratio=result["coverage_ratio"],
    )
    return result


def _write_skipped(
    out: Path,
    run_id: str,
    market_date: str,
    symbols: list[str],
    reason: str,
    *,
    status: str = "ZAPI_MISSING_CREDENTIAL",
    blocking: bool = False,
    request_count: int = 0,
    failure_count: int = 0,
) -> dict[str, Any]:
    rows = [{
        "run_id": run_id,
        "symbol": symbol,
        "status": "MISSING_ZAPI",
        "blocking": blocking,
        "selected_source": "YAHOO",
        "enrichment_source": "NONE",
        "error": reason,
    } for symbol in symbols]
    summary = {
        "run_id": run_id,
        "trade_date": market_date,
        "status": status,
        "reason": reason,
        "provider": "ZAPI_IDX",
        "execution_source": "YAHOO",
        "validation_source": "ZAPI_IDX",
        "source_mode": (
            "DISABLED" if status == "ZAPI_DISABLED"
            else "NOT_CONFIGURED" if status == "ZAPI_MISSING_CREDENTIAL"
            else "LIVE_FAILED"
        ),
        "endpoint_logical_names": ["/stock-summary"],
        "endpoint_request_counts": {"/stock-summary": request_count},
        "request_count": request_count,
        "success_count": 0,
        "failure_count": failure_count,
        "symbols_requested": len(symbols),
        "symbols_successful": 0,
        "validated": 0,
        "coverage_ratio": 0.0,
        "blocking": blocking,
        "blocking_failures": int(blocking and bool(symbols)),
        "reconciliation_counts": {"MISSING_ZAPI": len(symbols)},
        "selected_source": "YAHOO",
        "enrichment_source": "NONE",
        "warnings": [reason],
        "rows": rows,
    }
    return _write_outputs(out, run_id, market_date, rows, summary)


def _write_outputs(
    out: Path,
    run_id: str,
    market_date: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    safe_run_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in run_id)
    csv_path = out / f"yahoo_zapi_reconciliation_{market_date}_{safe_run_id}.csv"
    json_path = out / f"yahoo_zapi_reconciliation_{market_date}_{safe_run_id}.json"
    latest_csv = out / f"yahoo_zapi_reconciliation_{market_date}.csv"
    latest_json = out / f"yahoo_zapi_reconciliation_{market_date}.json"
    frame = pd.DataFrame(rows)
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    frame.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = dict(summary)
    payload["output_paths"] = {
        "csv": str(csv_path), "json": str(json_path),
        "latest_csv": str(latest_csv), "latest_json": str(latest_json),
    }
    payload["csv_path"] = str(csv_path)
    payload["json_path"] = str(json_path)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    json_path.write_text(rendered, encoding="utf-8")
    latest_json.write_text(rendered, encoding="utf-8")
    audit_path = out / "zapi_reconciliation_audit.jsonl"
    audit = {key: value for key, value in payload.items() if key != "rows"}
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(audit, ensure_ascii=False, default=str) + "\n")
    payload["audit_path"] = str(audit_path)
    return payload
