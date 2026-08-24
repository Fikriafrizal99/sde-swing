"""Compatibility import for the canonical Final Watchlist formatter.

The implementation lives in :mod:`modules.telegram.daily_report_ui` so the
runtime has exactly one Final Watchlist presentation path.
"""
from __future__ import annotations

from .daily_report_ui import format_watchlist_detail

__all__ = ["format_watchlist_detail"]
