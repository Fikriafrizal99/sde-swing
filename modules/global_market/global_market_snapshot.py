from __future__ import annotations

import time
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from swing_utils import dataframe_hash
from swing_utils import PACKAGE_VERSION

from modules.job_runner.runtime import RunnerContext, now_wib, resolve, write_json, read_json

from .global_market_registry import enabled_instruments, load_registry
from .global_market_scoring import compute_global_sentiment
from .global_market_validator import VALID, DELAYED_ACCEPTED, validate_instrument
from .yahoo_global_market_provider import YahooFetchResult, YahooGlobalMarketProvider


VALID_STATUSES = {VALID, DELAYED_ACCEPTED}


def _cache_path(instrument: dict[str, Any]) -> Path:
    key = str(instrument["key"]).replace("/", "_").replace("\\", "_")
    return resolve("data/cache/global_market") / f"{key}.json"


def _cache_valid(path: Path, max_age_minutes: int, fetched_at: datetime) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=fetched_at.tzinfo)
    return fetched_at - modified <= timedelta(minutes=max_age_minutes)


def _frame_to_cache(frame: pd.DataFrame, item: dict[str, Any]) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {"item": item, "frame": [], "dataframe_hash": dataframe_hash(pd.DataFrame())}
    work = frame.copy()
    if "Date" in work.columns:
        work["Date"] = pd.to_datetime(work["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return {
        "item": item,
        "frame": work.where(pd.notna(work), None).to_dict(orient="records"),
        "dataframe_hash": dataframe_hash(work),
    }


def _frame_from_cache(payload: dict[str, Any]) -> pd.DataFrame:
    rows = payload.get("frame", [])
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if "Date" in frame.columns:
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    return frame


def _fetch_with_retry(
    provider: YahooGlobalMarketProvider,
    symbols: list[str],
    registry: dict[str, Any],
) -> dict[str, YahooFetchResult]:
    retry_count = max(int(registry.get("retry_count", 2)), 1)
    retry_delay = float(registry.get("retry_delay_seconds", 3))
    period = str(registry.get("period", "10d"))
    interval = str(registry.get("interval", "1d"))
    timeout = int(registry.get("request_timeout_seconds", 20))
    pending = list(symbols)
    results: dict[str, YahooFetchResult] = {}
    for attempt in range(1, retry_count + 1):
        if not pending:
            break
        fetched = provider.download_batch(
            pending,
            period=period,
            interval=interval,
            timeout=timeout,
            threads=bool(registry.get("batch_fetch_enabled", True)),
        )
        next_pending: list[str] = []
        for symbol in pending:
            result = fetched.get(symbol, YahooFetchResult(symbol, pd.DataFrame(), "FETCH_FAILED", "missing fetch result"))
            result.retry_count = max(attempt - 1, 0)
            if result.status == "SUCCESS":
                results[symbol] = result
            else:
                results[symbol] = result
                next_pending.append(symbol)
        pending = next_pending
        if pending and attempt < retry_count and retry_delay > 0:
            time.sleep(retry_delay)
    return results


def load_existing_snapshot(ctx: RunnerContext) -> dict[str, Any]:
    path = resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"
    return read_json(path)


def build_global_market_snapshot(
    ctx: RunnerContext,
    registry_path: str | Path = "config/global_market.json",
    provider: YahooGlobalMarketProvider | None = None,
    preview_existing: bool | None = None,
    fallback_to_existing_on_failure: bool = False,
) -> dict[str, Any]:
    use_existing = ctx.preview_existing if preview_existing is None else bool(preview_existing)
    existing_before_refresh = load_existing_snapshot(ctx)
    if use_existing:
        if existing_before_refresh:
            existing_before_refresh["loaded_existing"] = True
            return existing_before_refresh
    registry = load_registry(registry_path)
    instruments = enabled_instruments(registry)
    fetched_at = now_wib()
    snapshot_id = f"GLOBAL-MARKET-{ctx.trade_date.strftime('%Y%m%d')}-{fetched_at.strftime('%H%M%S')}"
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    cache_used = 0
    cache_saved = 0
    fetch_items: list[dict[str, Any]] = []
    cache_enabled = bool(registry.get("cache_enabled", True))
    cache_age = int(registry.get("cache_max_age_minutes", 30))
    for instrument in instruments:
        cache_path = _cache_path(instrument)
        if cache_enabled and _cache_valid(cache_path, cache_age, fetched_at):
            cached = read_json(cache_path)
            frame = _frame_from_cache(cached)
            row = validate_instrument(instrument, frame, fetched_at, registry, cache_used=True)
            if row.get("freshness_status") in VALID_STATUSES:
                row.update({"weight": instrument.get("weight", 1.0), "inverse_sentiment": instrument.get("inverse_sentiment", False)})
                rows.append(row)
                cache_used += 1
                continue
        fetch_items.append(instrument)
    provider = provider or YahooGlobalMarketProvider()
    fetched = _fetch_with_retry(provider, [item["symbol"] for item in fetch_items], registry) if fetch_items else {}
    by_symbol = {item["symbol"]: item for item in fetch_items}
    for symbol, instrument in by_symbol.items():
        result = fetched.get(symbol, YahooFetchResult(symbol, pd.DataFrame(), "FETCH_FAILED", "missing fetch result"))
        row = validate_instrument(
            instrument,
            result.data,
            fetched_at,
            registry,
            result.status,
            result.error,
            retry_count=result.retry_count,
            cache_used=False,
        )
        row.update({"weight": instrument.get("weight", 1.0), "inverse_sentiment": instrument.get("inverse_sentiment", False)})
        rows.append(row)
        if row.get("freshness_status") not in VALID_STATUSES:
            warnings.append(f"{instrument['name']}: {row.get('freshness_status')}")
            if row.get("error"):
                errors.append(f"{instrument['key']}: {row.get('error')}")
        if cache_enabled and row.get("freshness_status") in VALID_STATUSES:
            write_json(_cache_path(instrument), _frame_to_cache(result.data, row))
            cache_saved += 1
    rows = sorted(rows, key=lambda row: str(row.get("instrument", "")))
    sentiment = compute_global_sentiment(rows, registry)
    coverage = float(sentiment.get("coverage_ratio", 0.0))
    minimum_coverage = float(registry.get("minimum_sentiment_coverage_ratio", 0.5))
    snapshot = {
        "schema_version": "1.7.0-multisource",
        "config_version": PACKAGE_VERSION,
        "snapshot_id": snapshot_id,
        "job_run_id": ctx.run_id,
        "trade_date": ctx.trade_date.isoformat(),
        "created_at": fetched_at.isoformat(timespec="seconds"),
        "provider": "YAHOO",
        "source_mode": "LIVE",
        "instruments": rows,
        "freshness_summary": _freshness_summary(rows),
        "coverage_ratio": coverage,
        "minimum_required_coverage_ratio": minimum_coverage,
        "global_sentiment": sentiment,
        "warnings": warnings,
        "errors": errors,
        "cache": {"enabled": cache_enabled, "used_count": cache_used, "saved_count": cache_saved, "max_age_minutes": cache_age},
        "source_metadata": {
            "provider": "YAHOO",
            "provider_status": "READY" if coverage >= minimum_coverage else "PARTIAL",
            "data_source_mode": str(registry.get("source_mode", "LIVE")).upper() or "LIVE",
            "source_coverage_ratio": coverage,
        },
    }
    snapshot["content_hash"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    out_dir = resolve("data/output/global_market") / ctx.trade_date.isoformat()
    write_json(out_dir / f"{snapshot_id}.json", snapshot)
    if coverage >= minimum_coverage:
        write_json(out_dir / "global_market_snapshot.json", snapshot)
        return snapshot

    # Jangan menimpa snapshot valid hari ini hanya karena refresh Yahoo terbaru gagal.
    existing_coverage = float(existing_before_refresh.get("coverage_ratio", 0.0) or 0.0) if existing_before_refresh else 0.0
    existing_trade_date = str(existing_before_refresh.get("trade_date", "")) if existing_before_refresh else ""
    if (
        fallback_to_existing_on_failure
        and existing_before_refresh
        and existing_trade_date == ctx.trade_date.isoformat()
        and existing_coverage >= minimum_coverage
    ):
        fallback = dict(existing_before_refresh)
        fallback["loaded_existing"] = True
        fallback["fallback_used"] = True
        fallback["fallback_reason"] = "LIVE_REFRESH_INSUFFICIENT_COVERAGE"
        fallback["refresh_attempt"] = {
            "snapshot_id": snapshot_id,
            "coverage_ratio": coverage,
            "warnings": warnings,
            "errors": errors,
        }
        fallback_warnings = [str(x) for x in fallback.get("warnings", [])]
        fallback_warnings.append(
            f"Refresh Yahoo terbaru gagal memenuhi coverage minimum ({coverage:.0%} < {minimum_coverage:.0%}); snapshot existing dipakai."
        )
        fallback["warnings"] = fallback_warnings
        fallback_errors = [str(x) for x in fallback.get("errors", [])]
        fallback_errors.extend([f"LIVE_REFRESH: {item}" for item in errors])
        fallback["errors"] = fallback_errors
        return fallback

    # Simpan hasil gagal terpisah agar penyebab dapat diaudit tanpa menjadikannya snapshot utama.
    write_json(out_dir / "global_market_snapshot_failed.json", snapshot)
    return snapshot


def _freshness_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        status = str(row.get("freshness_status", "UNKNOWN"))
        summary[status] = summary.get(status, 0) + 1
    return summary
