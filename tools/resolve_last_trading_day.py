#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
ROOT = Path(__file__).resolve().parents[1]


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
    candidate = day
    for _ in range(370):
        if is_trading_day(candidate, calendar):
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError("TRADING_DAY_NOT_FOUND_WITHIN_LOOKBACK")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve hari trading IDX terakhir untuk resend Final Watchlist")
    parser.add_argument("--date", default="", help="Tanggal acuan YYYY-MM-DD; default hari ini WIB")
    parser.add_argument("--calendar", default="config/trading_calendar.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    calendar_path = Path(args.calendar)
    if not calendar_path.is_absolute():
        calendar_path = ROOT / calendar_path
    calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
    reference = date.fromisoformat(args.date) if args.date else datetime.now(WIB).date()
    print(resolve_last_trading_day(reference, calendar).isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
