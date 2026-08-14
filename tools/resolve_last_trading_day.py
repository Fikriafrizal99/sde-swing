#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEZONE = "Asia/Jakarta"
DEFAULT_DATA_READY_TIME = "16:30"


def _date_set(raw) -> set[str]:
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    return set()


def is_trading_day(day: date, calendar: dict) -> bool:
    value = day.isoformat()
    special = _date_set(calendar.get("special_trading_days", []))
    holidays = _date_set(calendar.get("holidays", []))
    if value in special:
        return True
    if value in holidays:
        return False
    return day.weekday() < 5


def resolve_last_trading_day(day: date, calendar: dict) -> date:
    """Resolve the latest IDX trading date on or before an explicit date."""
    candidate = day
    for _ in range(370):
        if is_trading_day(candidate, calendar):
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError("TRADING_DAY_NOT_FOUND_WITHIN_LOOKBACK")


def _parse_clock(value: str) -> time:
    raw = str(value or "").strip()
    try:
        hour, minute = raw.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except Exception:
        return time(16, 30)


def resolve_last_completed_trading_day(
    now: datetime,
    calendar: dict,
    *,
    data_ready_time: str = DEFAULT_DATA_READY_TIME,
) -> date:
    """Return the latest IDX session whose end-of-day data should already exist.

    A trading calendar date is not considered complete until ``data_ready_time``.
    Before that cut-off, the resolver uses the preceding IDX session. Weekends
    and configured holidays naturally fall back to the most recent session.
    """
    ready_clock = _parse_clock(data_ready_time)
    today = now.date()

    if is_trading_day(today, calendar) and now.timetz().replace(tzinfo=None) >= ready_clock:
        candidate = today
    elif is_trading_day(today, calendar):
        candidate = today - timedelta(days=1)
    else:
        candidate = today

    return resolve_last_trading_day(candidate, calendar)


def _load_scheduler(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve sesi IDX terakhir yang sudah selesai untuk Portfolio Management"
    )
    parser.add_argument(
        "--date",
        default="",
        help="Tanggal acuan eksplisit YYYY-MM-DD; jika diisi, resolve on-or-before tanpa cut-off intraday",
    )
    parser.add_argument("--calendar", default="config/trading_calendar.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    calendar_path = Path(args.calendar)
    if not calendar_path.is_absolute():
        calendar_path = ROOT / calendar_path
    calendar = json.loads(calendar_path.read_text(encoding="utf-8-sig"))

    if args.date:
        resolved = resolve_last_trading_day(date.fromisoformat(args.date), calendar)
    else:
        scheduler_path = Path(args.scheduler_config)
        if not scheduler_path.is_absolute():
            scheduler_path = ROOT / scheduler_path
        scheduler = _load_scheduler(scheduler_path)
        timezone_name = str(scheduler.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
        broker_cfg = scheduler.get("broker_portfolio", {}) or {}
        post_market_cfg = scheduler.get("post_market", {}) or {}
        ready_time = str(
            broker_cfg.get("data_ready_time")
            or post_market_cfg.get("time")
            or DEFAULT_DATA_READY_TIME
        ).strip()
        now = datetime.now(ZoneInfo(timezone_name))
        resolved = resolve_last_completed_trading_day(
            now,
            calendar,
            data_ready_time=ready_time,
        )

    # Keep stdout date-only: RUN_POSITION_MANAGEMENT.bat consumes this directly.
    print(resolved.isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
