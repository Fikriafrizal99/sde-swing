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
REJECTION_SAMPLE_LIMIT = 12
_ORIGINAL_NORMALIZE = market.strict_normalize_result
_ORIGINAL_COLLECT = market.collect_market_news
_REJECTION_COUNTS: dict[str, int] = {}
_REJECTION_SAMPLES: list[dict[str, str]] = []


def _reset_diagnostics() -> None:
    _REJECTION_COUNTS.clear()
    _REJECTION_SAMPLES.clear()


def _record_rejection(reason: str, result: dict[str, Any], scope: str) -> None:
    key = str(reason or "unknown").strip().lower() or "unknown"
    _REJECTION_COUNTS[key] = _REJECTION_COUNTS.get(key, 0) + 1
    if len(_REJECTION_SAMPLES) >= REJECTION_SAMPLE_LIMIT:
        return

    url = str(result.get("url", "") or "").strip()
    _REJECTION_SAMPLES.append(
        {
            "scope": str(scope or "").upper(),
            "reason": key,
            "headline": str(result.get("title", "") or "").strip()[:220],
            "domain": base._domain(url),
            "age": str(result.get("age", "") or "").strip(),
            "published_at": str(
                result.get("page_age", "") or result.get("published_at", "") or ""
            ).strip(),
        }
    )


def _diagnose_market_rejection(
    result: dict[str, Any], *, scope: str, symbols: list[str]
) -> str:
    """Mirror the existing market filter only to report its first failed gate.

    This function is diagnostic-only. The actual accept/reject decision remains
    owned by ``market.strict_normalize_result``.
    """
    headline = str(result.get("title", "") or "").strip()
    url = str(result.get("url", "") or "").strip()
    description = str(result.get("description", "") or "").strip()
    if not headline or not url or base._blocked_source(url):
        return "invalid_or_blocked"
    if market._is_noise(headline, description):
        return "noise"

    scope_key = str(scope or "").upper()
    if scope_key not in market.DISPLAY_SCOPES:
        return "unsupported_scope"

    combined = f"{headline} {description}"
    symbol = base._extract_symbol(combined, symbols)
    impact_hits = market._hit_count(combined, market.MARKET_IMPACT_TERMS)
    movement_hits = market._hit_count(combined, market.MOVEMENT_EVENT_TERMS)
    indonesia_relevant = market._indonesia_relevant(combined, symbol)
    _, factual_hits, speculative_hits = market._event_quality(headline, description)
    category, category_hits = market._detect_event_category(
        combined,
        allowed=market.SCOPE_ALLOWED_CATEGORIES.get(scope_key),
    )

    if not category:
        return "no_category"
    if not market._source_allowed(
        url=url,
        scope=scope_key,
        category_hits=category_hits,
        indonesia_relevant=indonesia_relevant,
        symbol=symbol,
    ):
        return "source_policy"

    if scope_key == "GLOBAL":
        if impact_hits < 1:
            return "global_no_market_impact"
        if market._contains(combined, market.POLITICAL_TERMS):
            pathway_terms = (
                market.MARKET_IMPACT_TERMS
                | market.COMMODITY_CATALYST_TERMS
                | {
                    "tariff",
                    "tariffs",
                    "trade war",
                    "yield",
                    "dollar",
                    "stocks",
                    "equities",
                }
            )
            if market._hit_count(combined, pathway_terms) < 2:
                return "global_political_pathway"
        if speculative_hits >= 2 and factual_hits == 0:
            return "global_speculative"

    elif scope_key in {"INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK"}:
        if not indonesia_relevant:
            return "indonesia_relevance"
        if (
            scope_key == "SECTOR"
            and movement_hits < 1
            and impact_hits < 1
            and category_hits < 2
        ):
            return "sector_relevance"

    elif scope_key == "ISSUER":
        if not symbol:
            return "issuer_symbol"

    return "market_filter_other"


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


def strict_normalize_recent(
    result: dict[str, Any], *, scope: str, symbols: list[str]
) -> base.NewsItem | None:
    item = _ORIGINAL_NORMALIZE(result, scope=scope, symbols=symbols)
    if item is None:
        _record_rejection(
            _diagnose_market_rejection(result, scope=scope, symbols=symbols),
            result,
            scope,
        )
        return None
    if not _is_recent_item(item):
        _record_rejection("freshness", result, scope)
        return None
    return item


def collect_market_news_with_diagnostics(
    session: str,
    scheduler: dict[str, Any] | None = None,
) -> tuple[list[base.NewsItem], dict[str, Any]]:
    _reset_diagnostics()
    items, meta = _ORIGINAL_COLLECT(session, scheduler)
    enriched = dict(meta)
    enriched["rejected_results"] = sum(_REJECTION_COUNTS.values())
    enriched["rejection_counts"] = dict(sorted(_REJECTION_COUNTS.items()))
    enriched["rejection_samples"] = list(_REJECTION_SAMPLES)
    return items, enriched


def _freshness_for_scope(
    session: str, scope: str, config: dict[str, Any], current: datetime
) -> str:
    del scope
    return base._resolve_freshness(session, config, current)


def _limit_market_items(
    items: list[base.NewsItem], maximum: int | None = None
) -> list[base.NewsItem]:
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


def format_market_digest(
    session: str,
    items: list[base.NewsItem],
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or base.now_wib()
    title = (
        "📰 <b>SDE SWING — MORNING NEWS</b>"
        if session == "morning"
        else "📰 <b>SDE SWING — POST MARKET NEWS</b>"
    )
    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 {generated_at.strftime('%d %b %Y')} | {generated_at.strftime('%H:%M')} WIB",
        market._subtitle(session, generated_at),
        "━━━━━━━━━━━━━━━━━━━━",
    ]
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
    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 <b>{len(items)} berita berdampak terhadap market</b>",
            "",
            "ℹ️ News hanya informasi dan tidak memengaruhi keputusan SDE.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def install_policy() -> None:
    market.strict_normalize_result = strict_normalize_recent
    market.collect_market_news = collect_market_news_with_diagnostics
    market._freshness_for_scope = _freshness_for_scope
    market._limit_market_items = _limit_market_items
    market.format_market_digest = format_market_digest


def main() -> int:
    install_policy()
    return market.main()


if __name__ == "__main__":
    raise SystemExit(main())
