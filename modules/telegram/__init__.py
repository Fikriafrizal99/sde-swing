"""Telegram presentation package overrides.

The Market Outlook formatter is kept in its own module so presentation can
change independently from engine/report calculations.  Rebind only that
formatter; every other daily-report formatter remains untouched.
"""

from . import daily_report_ui as _daily_report_ui
from .market_outlook_ui import format_market_outlook as _market_outlook_formatter

_daily_report_ui.format_market_outlook = _market_outlook_formatter

__all__ = ["_daily_report_ui"]
