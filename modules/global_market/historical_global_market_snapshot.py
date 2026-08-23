from __future__ import annotations

"""Point-in-time global-market snapshot builder for missed Market Outlook runs.

This module is intentionally separate from the live snapshot path.  It does not
change normal Market Outlook acquisition or any scoring formula.  Recovery
requests Yahoo history only through the last market session that was knowable at
the historical Market Outlook timestamp, then reuses the existing validator and
global-sentiment scorer.
"""

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from swing_utils import PACKAGE_VERSION
from modules.job_runner.runtime import RunnerContext, now_wib, read_json, resolve, write_json

from .global_market_registry import enabled_instruments, load_registry
from .global_market_scoring import compute_global_sentiment
from .global_market_validator import (
    DELAYED_ACCEPTED,
    VALID,
    expected_last_session,
    validate_instrument,
)
from .yahoo_global_market_provider import YahooFetchResult, YahooGlobalMarketProvider


VALID_STATUSES = {VALID, DELAYED_ACCEPTED}
DEFAULT_LOOKBACK_DAYS = 45


def _freshness_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        status = str(row.get("freshness_status", "UNKNOWN"))
        summary[status] = summary.get(status, 0) + 1
    return summary


def _safe_existing_snapshot(path: Path, trade_date: date, as_of_at: datetime) -> dict[str, Any]:
    """Reuse only an authentic same-day live snapshot or matching recovery."""
    payload = read_json(path)
    if not isinstance(payload, dict) or not payload:
        return {}
    if str(payload.get("trade_date") or "") != trade_date.isoformat():
        return {}

    source_mode = str(payload.get("source_mode") or "").upper()
    recovery_as_of = str(payload.get("recovery_as_of") or "")
    if source_mode == "HISTORICAL_AS_OF" and recovery_as_of == as_of_at.isoformat(timespec="seconds"):
        clone = dict(payload)
        clone["loaded_existing"] = True
        return clone

    created_raw = str(payload.get("created_at") or "")
    try:
        created = datetime.fromisoformat(created_raw)
        if created.tzinfo is None:
            created = created.replace(tzinfo=as_of_at.tzinfo)
        created_local = created.astimezone(as_of_at.tzinfo)
    except Exception:
        return {}

    # A live snapshot actually created on the target trading day is more
    # authentic than a reconstruction.  A later-created LIVE snapshot is not
    # reused because it may have been generated with future information.
    if source_mode == "LIVE" and created_local.date() == trade_date:
        clone = dict(payload)
        clone["loaded_existing"] = True
        clone["recovery_reused_authentic_live_snapshot"] = True
        return clone
    return {}


def _download_expected_session_group(
    provider: YahooGlobalMarketProvider,
    items: list[dict[str, Any]],
    *,
    expected: date,
    lookback_days: int,
    interval: str,
    timeout: int,
    threads: bool,
) -> dict[str, YahooFetchResult]:
    start = (expected - timedelta(days=max(int(lookback_days), 10))).isoformat()
    end = (expected + timedelta(days=1)).isoformat()  # yfinance end is exclusive
    return provider.download_batch_range(
        [str(item["symbol"]) for item in items],
        start=start,
        end=end,
        interval=interval,
        timeout=timeout,
        threads=threads,
    )


def build_historical_global_market_snapshot(
    ctx: RunnerContext,
    *,
    as_of_at: datetime,
    registry_path: str | Path = "config/global_market.json",
    provider: YahooGlobalMarketProvider | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    force: bool = False,
) -> dict[str, Any]:
    """Build an auditable global-market snapshot as it was knowable at ``as_of_at``.

    The existing validator remains authoritative for expected-session and
    freshness logic.  Instruments are grouped by that expected session before
    download, and each Yahoo request is bounded to an exclusive end date of
    ``expected + 1 day``.  Thus neither the transport nor the validator needs a
    post-as-of daily row to reconstruct the snapshot.
    """
    if as_of_at.tzinfo is None:
        raise ValueError("HISTORICAL_AS_OF_REQUIRES_TIMEZONE")
    if as_of_at.date() != ctx.trade_date:
        raise ValueError(
            f"HISTORICAL_AS_OF_DATE_MISMATCH:{as_of_at.date().isoformat()}!={ctx.trade_date.isoformat()}"
        )

    canonical_path = resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"
    if not force:
        existing = _safe_existing_snapshot(canonical_path, ctx.trade_date, as_of_at)
        if existing:
            return existing

    registry = load_registry(registry_path)
    instruments = enabled_instruments(registry)
    if not instruments:
        raise RuntimeError("GLOBAL_MARKET_INSTRUMENTS_EMPTY")

    actual_fetched_at = now_wib()
    interval = str(registry.get("interval", "1d"))
    timeout = int(registry.get("request_timeout_seconds", 20))
    threads = bool(registry.get("batch_fetch_enabled", True))
    lookback = int(registry.get("historical_recovery_lookback_days", lookback_days) or lookback_days)
    provider = provider or YahooGlobalMarketProvider()

    groups: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for instrument in instruments:
        groups[expected_last_session(as_of_at, instrument, registry)].append(instrument)

    results: dict[str, YahooFetchResult] = {}
    transport_ranges: list[dict[str, Any]] = []
    for expected, items in sorted(groups.items(), key=lambda pair: pair[0]):
        start = (expected - timedelta(days=max(lookback, 10))).isoformat()
        end = (expected + timedelta(days=1)).isoformat()
        transport_ranges.append({
            "expected_session": expected.isoformat(),
            "start": start,
            "end_exclusive": end,
            "symbols": [str(item["symbol"]) for item in items],
        })
        fetched = _download_expected_session_group(
            provider,
            items,
            expected=expected,
            lookback_days=lookback,
            interval=interval,
            timeout=timeout,
            threads=threads,
        )
        results.update(fetched)

    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    for instrument in instruments:
        symbol = str(instrument["symbol"])
        result = results.get(
            symbol,
            YahooFetchResult(symbol, __import__("pandas").DataFrame(), "FETCH_FAILED", "missing historical fetch result"),
        )
        row = validate_instrument(
            instrument,
            result.data,
            as_of_at,
            registry,
            result.status,
            result.error,
            retry_count=result.retry_count,
            cache_used=False,
        )
        row.update({
            "weight": instrument.get("weight", 1.0),
            "inverse_sentiment": instrument.get("inverse_sentiment", False),
            "actual_recovery_fetch_at": actual_fetched_at.isoformat(timespec="seconds"),
        })
        rows.append(row)
        if row.get("freshness_status") not in VALID_STATUSES:
            warnings.append(f"{instrument['name']}: {row.get('freshness_status')}")
            if row.get("error"):
                errors.append(f"{instrument['key']}: {row.get('error')}")

    rows = sorted(rows, key=lambda row: str(row.get("instrument", "")))
    sentiment = compute_global_sentiment(rows, registry)
    coverage = float(sentiment.get("coverage_ratio", 0.0) or 0.0)
    minimum_coverage = float(registry.get("minimum_sentiment_coverage_ratio", 0.5))
    snapshot_id = (
        f"GLOBAL-MARKET-RECOVERY-{ctx.trade_date.strftime('%Y%m%d')}-"
        f"{actual_fetched_at.strftime('%H%M%S')}"
    )
    snapshot = {
        "schema_version": PACKAGE_VERSION,
        "config_version": PACKAGE_VERSION,
        "snapshot_id": snapshot_id,
        "job_run_id": ctx.run_id,
        "trade_date": ctx.trade_date.isoformat(),
        "created_at": actual_fetched_at.isoformat(timespec="seconds"),
        "recovery_as_of": as_of_at.isoformat(timespec="seconds"),
        "recovery_mode": "HISTORICAL_AS_OF",
        "provider": "YAHOO",
        "source_mode": "HISTORICAL_AS_OF",
        "instruments": rows,
        "freshness_summary": _freshness_summary(rows),
        "coverage_ratio": coverage,
        "minimum_required_coverage_ratio": minimum_coverage,
        "global_sentiment": sentiment,
        "warnings": warnings,
        "errors": errors,
        "cache": {"enabled": False, "used_count": 0, "saved_count": 0},
        "historical_transport_ranges": transport_ranges,
        "source_metadata": {
            "provider": "YAHOO",
            "provider_status": "READY" if coverage >= minimum_coverage else "PARTIAL",
            "data_source_mode": "HISTORICAL_AS_OF",
            "source_coverage_ratio": coverage,
            "point_in_time_safe": True,
            "recovery_as_of": as_of_at.isoformat(timespec="seconds"),
        },
    }
    snapshot["content_hash"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()

    out_dir = canonical_path.parent
    write_json(out_dir / f"{snapshot_id}.json", snapshot)
    if coverage >= minimum_coverage:
        write_json(canonical_path, snapshot)
        return snapshot

    write_json(out_dir / "global_market_snapshot_recovery_failed.json", snapshot)
    return snapshot
