from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def normalized_holidays(values: Iterable[Any]) -> set[date]:
    holidays: set[date] = set()
    for value in values or []:
        raw = value.get("date") if isinstance(value, dict) else value
        if raw:
            holidays.add(_as_date(raw))
    return holidays


def is_idx_trading_day(value: Any, holidays: Iterable[Any] = (), special_trading_days: Iterable[Any] = ()) -> bool:
    day = _as_date(value)
    special = normalized_holidays(special_trading_days)
    if day in special:
        return True
    if day.weekday() >= 5:
        return False
    return day not in normalized_holidays(holidays)


def validate_market_date(value: Any, *, holidays: Iterable[Any] = (), special_trading_days: Iterable[Any] = ()) -> date:
    day = _as_date(value)
    if not is_idx_trading_day(day, holidays, special_trading_days):
        raise ValueError(f"NON_TRADING_MARKET_DATE: {day.isoformat()}")
    return day


def previous_idx_trading_day(
    value: Any,
    *,
    holidays: Iterable[Any] = (),
    special_trading_days: Iterable[Any] = (),
) -> date:
    """Return the preceding BEI session, skipping weekends and configured holidays."""
    probe = _as_date(value) - timedelta(days=1)
    while not is_idx_trading_day(probe, holidays, special_trading_days):
        probe -= timedelta(days=1)
    return probe
