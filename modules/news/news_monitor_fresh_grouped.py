#!/usr/bin/env python3
from __future__ import annotations

import html
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.news import news_monitor as base
from modules.news import news_monitor_market_impact as market

RECENT_NEWS_MAX_HOURS = 24.0
_ORIGINAL_NORMALIZE = market.strict_normalize_result


def _age_hours(value: str) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"now", "just now", "baru saja"}:
        return 0.0
    if text in {"yesterday", "kemarin"}:
        return 24.0
    for pattern, multiplier in (
        (r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|min|m)\b", 1.0 / 60.0),
        (r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|hr|h)\b", 1.0),
        (r"(\d+(?:\.\d+)?)\s*(?:days?|day|d)\b", 24.0),
    ):
        match = re.search(pattern, text)
        if match:
            return float(match.group(1)) * multiplier
    return None


def _published_age_hours(value: str, current: datetime) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=base.WIB)
    return (current - published.astimezone(base.WIB)).total_seconds() / 3600.0


def _is_recent_item(item: base.NewsItem, current: datetime | None = None) -> bool:
    current = current or base.now_wib()
    hours = _age_hours(item.age)
    if hours is None:
        hours = _published_age_hours(item.published_at, current)
    return hours is not None and 0.0 <= hours <= RECENT_NEWS_MAX_HOURS


def strict_normalize_recent(result: dict[str, Any], *, scope: str, symbols: list[str]) -> base.NewsItem | None:
    item = _ORIGINAL_NORMALIZE(result, scope=scope, symbols=symbols)
    return item if item is not None and _is_recent_item(item) else None


def _freshness_for_scope(session: str, scope: str, config: dict[str, Any], current: datetime) -> str:
    del scope
    return base._resolve_freshness(session, config, current)


def _limit_market_items(items: list[base.NewsItem], maximum: int | None = None) -> list[base.NewsItem]:
    del maximum
    return sorted(items, key=lambda item: item.score, reverse=True)


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _render_article(lines: list[str], scope: str, item: base.NewsItem) -> None:
    headline = item.headline
    if scope == "ISSUER" and item.symbol and not headline.upper().startswith(item.symbol.upper()):
        headline = f"{item.symbol} — {headline}"
    lines.append(f"◆ <b>{_esc(headline)}</b>")
    source_line = _esc(item.source)
    time_text = base._item_time(item)
    if time_text:
        source_line += f" | {_esc(time_text)}"
    lines.append(source_line)
    lines.append(f"🔗 Baca: {_esc(item.url)}")
    lines.append("")


def format_market_digest(session: str, items: list[base.NewsItem], generated_at: datetime | None = None) -> str:
    generated_at = generated_at or base.now_wib()
    title = "📰 <b>SDE SWING — MORNING NEWS</b>" if session == "morning" else "📰 <b>SDE SWING — POST MARKET NEWS</b>"
    lines = [title, "━━━━━━━━━━━━━━━━━━━━", f"📅 {generated_at.strftime('%d %b %Y')} | {generated_at.strftime('%H:%M')} WIB", market._subtitle(session, generated_at), "━━━━━━━━━━━━━━━━━━━━"]
    sections = [
        ("GLOBAL", "🌍 <b>GLOBAL &amp; MACRO</b>"),
        ("INDONESIA", "🇮🇩 <b>INDONESIA MARKET</b>"),
        ("INDEX", "📊 <b>INDEX &amp; REBALANCING</b>"),
        ("SECTOR", "🏭 <b>SECTOR &amp; COMMODITY</b>"),
        ("CORPORATE", "🏢 <b>CORPORATE EVENTS</b>"),
        ("RISK", "⚠️ <b>RISK &amp; EXCHANGE</b>"),
        ("ISSUER", "📌 <b>EMITEN TERPANTAU</b>"),
    ]
    for scope, heading in sections:
        group = [item for item in items if item.scope == scope]
        if not group:
            continue
        lines.extend(["", heading, ""])
        categories: list[str] = []
        for item in group:
            category = str(item.category or "").strip()
            if category not in categories:
                categories.append(category)
        for category in categories:
            lines.extend([f"🏷 <b>{_esc(market._category_label(category))}</b>", ""])
            for item in group:
                if str(item.category or "").strip() == category:
                    _render_article(lines, scope, item)
    lines.extend(["━━━━━━━━━━━━━━━━━━━━", f"📊 <b>{len(items)} berita berdampak terhadap market</b>", "", "ℹ️ News hanya informasi dan tidak memengaruhi keputusan SDE."])
    return "\n".join(lines).strip() + "\n"


def install_policy() -> None:
    market.strict_normalize_result = strict_normalize_recent
    market._freshness_for_scope = _freshness_for_scope
    market._limit_market_items = _limit_market_items
    market.format_market_digest = format_market_digest


def main() -> int:
    install_policy()
    return market.main()


if __name__ == "__main__":
    raise SystemExit(main())
