"""Telegram presentation package overrides.

Market Outlook and Post Market formatters are kept in dedicated modules so
presentation can evolve independently from engine/report calculations.  Only
those human-facing formatters are rebound here; engine-owned artifacts remain
untouched.
"""

from . import daily_report_ui as _daily_report_ui
from .market_outlook_ui import format_market_outlook as _market_outlook_formatter
from .post_market_ui import format_post_market as _post_market_formatter

_daily_report_ui.format_market_outlook = _market_outlook_formatter
_daily_report_ui.format_post_market = _post_market_formatter

__all__ = ["_daily_report_ui"]
