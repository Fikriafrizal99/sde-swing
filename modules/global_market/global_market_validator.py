from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


VALID = "VALID"
DELAYED_ACCEPTED = "DELAYED_ACCEPTED"
STALE = "STALE"
DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
FETCH_FAILED = "FETCH_FAILED"
INVALID_PRICE = "INVALID_PRICE"
INVALID_DATE = "INVALID_DATE"


def _business_day(day: date) -> bool:
    return day.weekday() < 5


def previous_business_day(day: date) -> date:
    probe = day - timedelta(days=1)
    while not _business_day(probe):
        probe -= timedelta(days=1)
    return probe


def expected_last_session(fetched_at: datetime, instrument: dict[str, Any], registry: dict[str, Any]) -> date:
    category = str(instrument.get("category", "")).upper()
    freshness = registry.get("freshness", {}).get(category, {})
    tz_name = str(instrument.get("timezone") or freshness.get("timezone") or "UTC")
    close_text = str(instrument.get("market_close") or freshness.get("market_close") or "16:00")
    hour, minute = [int(x) for x in close_text.split(":", 1)]
    local = fetched_at.astimezone(ZoneInfo(tz_name))
    expected = local.date()
    if not _business_day(expected):
        return previous_business_day(expected)
    if local.time() < time(hour, minute):
        return previous_business_day(expected)
    return expected


def freshness_status(market_date: date, expected_date: date, instrument: dict[str, Any], registry: dict[str, Any]) -> str:
    category = str(instrument.get("category", "")).upper()
    freshness = registry.get("freshness", {}).get(category, {})
    accepted = int(freshness.get("accepted_delay_days", 1))
    max_age = int(freshness.get("max_age_days", 5))
    if market_date > expected_date + timedelta(days=1):
        return INVALID_DATE
    age = (expected_date - market_date).days
    if age <= 0:
        return VALID
    if age <= accepted:
        return DELAYED_ACCEPTED
    if age <= max_age:
        return STALE
    return STALE


def validate_instrument(
    instrument: dict[str, Any],
    frame: pd.DataFrame,
    fetched_at: datetime,
    registry: dict[str, Any],
    fetch_status: str = "SUCCESS",
    error: str = "",
    retry_count: int = 0,
    cache_used: bool = False,
) -> dict[str, Any]:
    base = {
        "instrument": instrument["key"],
        "display_name": instrument["name"],
        "yahoo_symbol": instrument["symbol"],
        "category": instrument["category"],
        "provider": "YAHOO",
        "market_date": None,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "previous_close": None,
        "change_point": None,
        "change_pct": None,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "freshness_status": DATA_NOT_AVAILABLE,
        "market_status": "",
        "error": error,
        "is_fallback": False,
        "cache_used": bool(cache_used),
        "retry_count": int(retry_count),
    }
    if fetch_status != "SUCCESS":
        base["freshness_status"] = FETCH_FAILED
        base["error"] = error or fetch_status
        return base
    if frame is None or frame.empty:
        base["freshness_status"] = DATA_NOT_AVAILABLE
        base["error"] = error or "Yahoo data kosong"
        return base
    work = frame.copy()
    if "Date" not in work.columns or "Close" not in work.columns:
        base["freshness_status"] = INVALID_DATE
        base["error"] = "schema Date/Close tidak tersedia"
        return base
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    work["Close"] = pd.to_numeric(work["Close"], errors="coerce")
    work = work.dropna(subset=["Date"]).sort_values("Date")
    if work.empty:
        base["freshness_status"] = INVALID_DATE
        base["error"] = "tanggal Yahoo tidak valid"
        return base
    expected = expected_last_session(fetched_at, instrument, registry)
    work["_date_only"] = work["Date"].dt.date
    closed = work[(work["_date_only"] <= expected) & work["Close"].notna() & (work["Close"].astype(float) > 0)].copy()
    if closed.empty:
        base["freshness_status"] = INVALID_PRICE
        base["error"] = "tidak ada close valid untuk sesi market yang sudah selesai"
        return base
    work = closed
    latest = work.iloc[-1]
    close = latest.get("Close")
    if pd.isna(close) or float(close) <= 0:
        base["freshness_status"] = INVALID_PRICE
        base["error"] = "close kosong atau tidak valid"
        return base
    previous_close = latest.get("Previous_Close")
    if pd.isna(previous_close):
        closed = work.dropna(subset=["Close"])
        if len(closed) >= 2:
            previous_close = closed.iloc[-2]["Close"]
    if pd.isna(previous_close) or float(previous_close) <= 0:
        base["freshness_status"] = INVALID_PRICE
        base["error"] = "previous close kosong atau tidak valid"
        return base
    market_date = pd.Timestamp(latest["Date"]).date()
    status = freshness_status(market_date, expected, instrument, registry)
    change = float(close) - float(previous_close)
    base.update({
        "market_date": market_date.isoformat(),
        "open": _maybe_float(latest.get("Open")),
        "high": _maybe_float(latest.get("High")),
        "low": _maybe_float(latest.get("Low")),
        "close": float(close),
        "previous_close": float(previous_close),
        "change_point": change,
        "change_pct": (change / float(previous_close)) * 100.0,
        "freshness_status": status,
        "expected_market_date": expected.isoformat(),
    })
    if status in {STALE, INVALID_DATE}:
        base["error"] = f"market date {market_date.isoformat()} tidak fresh terhadap expected {expected.isoformat()}"
    return base


def _maybe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None
