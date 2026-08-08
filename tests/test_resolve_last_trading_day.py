from datetime import date

from tools.resolve_last_trading_day import is_trading_day, resolve_last_trading_day


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


def test_current_trading_day_is_kept():
    assert resolve_last_trading_day(date(2026, 8, 7), _calendar()) == date(2026, 8, 7)


def test_special_trading_day_can_override_weekend():
    calendar = _calendar()
    calendar["special_trading_days"] = ["2026-08-08"]
    assert is_trading_day(date(2026, 8, 8), calendar)
    assert resolve_last_trading_day(date(2026, 8, 8), calendar) == date(2026, 8, 8)
