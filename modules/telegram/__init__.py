"""Telegram presentation package overrides.

Market Outlook, Post Market, and Final Watchlist formatters are kept in
dedicated modules so presentation can evolve independently from engine/report
calculations. Only those human-facing formatters are rebound here; engine-owned
artifacts remain untouched.
"""

import logging

# Matplotlib can emit noisy findfont fallback warnings on some Windows hosts when
# DejaVu Sans resolves medium/semibold requests to its bundled regular/bold
# faces. The renderer is deterministic and the fallback is intentional, so keep
# those messages out of the operator console without changing report behavior.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

from . import daily_report_ui as _daily_report_ui
from .market_outlook_ui import format_market_outlook as _market_outlook_formatter
from .post_market_ui import format_post_market as _post_market_formatter

# Final Watchlist has one canonical formatter in ``daily_report_ui``. Keep a
# named compatibility alias without rebinding it to a second implementation.
_daily_report_ui.format_final_watchlist_detail = _daily_report_ui.format_watchlist_detail
_daily_report_ui.format_market_outlook = _market_outlook_formatter
_daily_report_ui.format_post_market = _post_market_formatter

__all__ = ["_daily_report_ui"]
