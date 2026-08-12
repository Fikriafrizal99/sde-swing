from datetime import date, datetime
from zoneinfo import ZoneInfo

from tools.resolve_last_trading_day import (
    is_trading_day,
    resolve_last_completed_trading_day,
    resolve_last_trading_day,
)


JAKARTA = ZoneInfo("Asia/Jakarta")


def _calendar():
    return {
        "holidays": {
            "2026-08-17": {"holiday": True},
        },
        "special_trading_days": [],
    }


def test_weekend_resolves_to_friday():
    assert resolve_last_trading_day(date(2026, 8, 8), _calendar()) == date(2026, 8, 7)


def test_holiday_monday_resolves_to_previous_friday():
    assert resolve_last_trading_day(date(2026, 8, 17), _calendar()) == date(2026, 8, 14)


def test_current_trading_day_is_kept_for_explicit_date_resolution():
    assert resolve_last_trading_day(date(2026, 8, 7), _calendar()) == date(2026, 8, 7)


def test_special_trading_day_can_override_weekend():
    calendar = _calendar()
    calendar["special_trading_days"] = ["2026-08-08"]
    assert is_trading_day(date(2026, 8, 8), calendar)
    assert resolve_last_trading_day(date(2026, 8, 8), calendar) == date(2026, 8, 8)


def test_midnight_next_day_uses_previous_completed_idx_session():
    resolved = resolve_last_completed_trading_day(
        datetime(2026, 8, 13, 0, 35, tzinfo=JAKARTA),
        _calendar(),
        data_ready_time="16:30",
    )
    assert resolved == date(2026, 8, 12)


def test_trading_day_before_cutoff_still_uses_previous_session():
    resolved = resolve_last_completed_trading_day(
        datetime(2026, 8, 13, 15, 0, tzinfo=JAKARTA),
        _calendar(),
        data_ready_time="16:30",
    )
    assert resolved == date(2026, 8, 12)


def test_trading_day_after_cutoff_uses_today_session():
    resolved = resolve_last_completed_trading_day(
        datetime(2026, 8, 13, 16, 31, tzinfo=JAKARTA),
        _calendar(),
        data_ready_time="16:30",
    )
    assert resolved == date(2026, 8, 13)


def test_weekend_completed_session_uses_latest_friday():
    resolved = resolve_last_completed_trading_day(
        datetime(2026, 8, 15, 10, 0, tzinfo=JAKARTA),
        _calendar(),
        data_ready_time="16:30",
    )
    assert resolved == date(2026, 8, 14)
