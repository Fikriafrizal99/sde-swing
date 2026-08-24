from __future__ import annotations

"""Canonical human-facing FTJ branding for SDE Swing reports.

This module owns report titles only. It must never alter engine facts, scores,
decisions, trade plans, lifecycle state, news selection, or Telegram routing.
IDX Disclosure / Keterbukaan Informasi is intentionally outside this branding
contract and must remain unchanged.
"""

BRAND_NAME = "FTJ"
BRAND_EXPANSION = "Fikri Trade Journal"
COMMUNITY_NAME = "FTJ Community"

TITLE_MARKET_PULSE = "FTJ — MARKET PULSE"
TITLE_CLOSING_PULSE = "FTJ — CLOSING PULSE"
TITLE_BROKER_FLOW = "FTJ — BROKER FLOW"
TITLE_SMART_MONEY_FLOW = "FTJ — SMART MONEY FLOW"
TITLE_SWING_WATCHLIST = "FTJ — SWING WATCHLIST"
TITLE_ACTIVE_SETUPS = "FTJ — ACTIVE SETUPS"
TITLE_POSITION_UPDATE = "FTJ — POSITION UPDATE"
TITLE_MORNING_BRIEF = "FTJ — MORNING BRIEF"
TITLE_MARKET_NEWS = "FTJ — MARKET NEWS"
TITLE_MARKET_HEATMAP = "FTJ — MARKET HEATMAP"

# Specific names are replaced before the generic SDE SWING prefix fallback.
_TITLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("SDE SWING — MARKET OUTLOOK", TITLE_MARKET_PULSE),
    ("SDE SWING — POST MARKET NEWS", TITLE_MARKET_NEWS),
    ("SDE SWING — MORNING NEWS", TITLE_MORNING_BRIEF),
    ("SDE SWING — POST MARKET", TITLE_CLOSING_PULSE),
    ("SDE SWING — BROKER SUMMARY", TITLE_BROKER_FLOW),
    ("SDE SWING — BROKER MULTI-DAY", TITLE_SMART_MONEY_FLOW),
    ("SDE SWING — FINAL WATCHLIST", TITLE_SWING_WATCHLIST),
    ("SDE SWING — ACTIVE RECOMMENDATIONS", TITLE_ACTIVE_SETUPS),
    ("SDE SWING — LIFECYCLE DIGEST", TITLE_POSITION_UPDATE),
    ("SDE SWING — MARKET HEATMAP", TITLE_MARKET_HEATMAP),
)

_IDX_EXCLUSIONS = (
    "SDE SWING — IDX DISCLOSURE",
    "IDX DISCLOSURE WATCHER",
    "KETERBUKAAN INFORMASI",
)


def apply_ftj_branding(text: str) -> str:
    """Apply FTJ titles without touching IDX Disclosure presentation.

    The function is intentionally deterministic and idempotent so every output
    path can call it safely, including preview/resend and runtime delivery.
    """
    rendered = str(text or "")
    if not rendered:
        return rendered
    upper = rendered.upper()
    if any(marker in upper for marker in _IDX_EXCLUSIONS):
        return rendered
    for old, new in _TITLE_REPLACEMENTS:
        rendered = rendered.replace(old, new)
    # Compatibility fallback for older non-IDX human-facing SDE reports.
    rendered = rendered.replace("SDE SWING — ", "FTJ — ")
    return rendered


__all__ = [
    "BRAND_NAME",
    "BRAND_EXPANSION",
    "COMMUNITY_NAME",
    "TITLE_MARKET_PULSE",
    "TITLE_CLOSING_PULSE",
    "TITLE_BROKER_FLOW",
    "TITLE_SMART_MONEY_FLOW",
    "TITLE_SWING_WATCHLIST",
    "TITLE_ACTIVE_SETUPS",
    "TITLE_POSITION_UPDATE",
    "TITLE_MORNING_BRIEF",
    "TITLE_MARKET_NEWS",
    "TITLE_MARKET_HEATMAP",
    "apply_ftj_branding",
]
