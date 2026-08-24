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

# Runtime policy: local-scope relevance must be anchored to Indonesia itself,
# not generic macro words such as "yield", "inflation", or "rates".
STRICT_INDONESIA_ANCHORS = {
    "indonesia",
    "indonesian",
    "ihsg",
    "idx",
    "bei",
    "bursa efek indonesia",
    "saham indonesia",
    "pasar saham indonesia",
    "pasar modal indonesia",
    "rupiah",
    "bank indonesia",
    "bi rate",
    "ojk",
    "jakarta composite",
    "jci",
    "pemerintah indonesia",
    "kementerian keuangan indonesia",
}

LOCAL_SCOPES = {"INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK"}
RESCUE_SCOPES = {"GLOBAL", "INDONESIA"}

_ORIGINAL_NORMALIZE = market.strict_normalize_result
_ORIGINAL_COLLECT = market.collect_market_news
_REJECTION_COUNTS: dict[str, int] = {}
_REJECTION_SAMPLES: list[dict[str, str]] = []
_RESCUE_COUNTS: dict[str, int] = {}


def _reset_diagnostics() -> None:
    _REJECTION_COUNTS.clear()
    _REJECTION_SAMPLES.clear()
    _RESCUE_COUNTS.clear()


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


def _record_rescue(reason: str) -> None:
    key = str(reason or "other").strip().lower() or "other"
    _RESCUE_COUNTS[key] = _RESCUE_COUNTS.get(key, 0) + 1


def _strict_indonesia_relevant(text: str, symbol: str = "") -> bool:
    return bool(symbol) or market._contains(text, STRICT_INDONESIA_ANCHORS)


def _focused_market_query_plan(symbols: list[str]) -> list[dict[str, str]]:
    """Use explicit scope anchors so Brave returns fewer cross-scope results."""
    plans = [
        {
            "scope": "GLOBAL",
            "query": (
                "global markets Federal Reserve Fed rates CPI payroll Treasury yields "
                "dollar DXY stocks oil gold China tariffs sanctions official"
            ),
        },
        {
            "scope": "INDONESIA",
            "query": (
                "Indonesia IHSG rupiah Bank Indonesia BI Rate IDX BEI OJK "
                "saham Indonesia pasar modal Indonesia inflasi SBN foreign flow"
            ),
        },
        {
            "scope": "INDEX",
            "query": (
                "MSCI Indonesia FTSE Indonesia LQ45 IDX30 IDX80 Indonesia "
                "index review rebalancing inclusion exclusion free float passive flow"
            ),
        },
        {
            "scope": "SECTOR",
            "query": (
                "Indonesia IDX stocks coal nickel gold CPO crude oil copper tin "
                "commodity prices mining energy palm oil production export"
            ),
        },
        {
            "scope": "CORPORATE",
            "query": (
                "Indonesia IDX issuer rights issue private placement buyback stock split "
                "dividend earnings profit guidance contract tender project order book "
                "acquisition merger strategic investor controlling shareholder"
            ),
        },
        {
            "scope": "RISK",
            "query": (
                "Indonesia IDX issuer suspension UMA delisting relisting bond refinancing "
                "default covenant rating downgrade fire shutdown force majeure "
                "production halt permit revoked"
            ),
        },
    ]
    if symbols:
        joined = " ".join(symbols[:12])
        plans.append(
            {
                "scope": "ISSUER",
                "query": (
                    f"{joined} Indonesia IDX earnings laba guidance dividend rights issue "
                    "buyback private placement contract tender project acquisition merger "
                    "suspension UMA bond default rating fire shutdown force majeure "
                    "foreign flow management director material transaction"
                ),
            }
        )
    return plans


def _strong_market_evidence(
    *,
    impact_hits: int,
    movement_hits: int,
    factual_hits: int,
    speculative_hits: int,
) -> bool:
    return (
        impact_hits >= 2
        and (factual_hits >= 1 or movement_hits >= 1)
        and speculative_hits <= 1
    )


def _resolve_category(
    *,
    scope: str,
    combined: str,
    impact_hits: int,
    movement_hits: int,
    factual_hits: int,
    speculative_hits: int,
    indonesia_relevant: bool,
) -> tuple[str, int, bool]:
    category, category_hits = market._detect_event_category(
        combined,
        allowed=market.SCOPE_ALLOWED_CATEGORIES.get(scope),
    )
    if category:
        return category, category_hits, False

    strong = _strong_market_evidence(
        impact_hits=impact_hits,
        movement_hits=movement_hits,
        factual_hits=factual_hits,
        speculative_hits=speculative_hits,
    )
    if scope == "GLOBAL" and strong:
        return "GLOBAL_CATALYST", 0, True
    if scope == "INDONESIA" and indonesia_relevant and strong:
        return "MACRO_INDONESIA", 0, True
    return "", 0, False


def _resilient_source_allowed(
    *,
    url: str,
    scope: str,
    category_hits: int,
    indonesia_relevant: bool,
    symbol: str,
    impact_hits: int,
    movement_hits: int,
    factual_hits: int,
    speculative_hits: int,
) -> bool:
    tier = market._source_tier(url)
    if tier == "REJECT":
        return False
    if tier in {"A", "B"}:
        return True

    # Tier-C sources may enter broad runtime news only with stronger content
    # evidence. This avoids a brittle domain whitelist while keeping clickbait out.
    strong = _strong_market_evidence(
        impact_hits=impact_hits,
        movement_hits=movement_hits,
        factual_hits=factual_hits,
        speculative_hits=speculative_hits,
    )
    if scope == "GLOBAL":
        return strong
    if scope == "INDONESIA":
        return indonesia_relevant and strong

    # Keep the existing strict rule for material local / issuer event scopes.
    return (
        scope in {"INDEX", "CORPORATE", "RISK", "ISSUER"}
        and indonesia_relevant
        and category_hits >= 2
        and (bool(symbol) or scope != "ISSUER")
    )


def _resilient_normalize_result(
    result: dict[str, Any], *, scope: str, symbols: list[str]
) -> tuple[base.NewsItem | None, str]:
    """Selective rescue for robust broad news; never relax issuer identity."""
    scope_key = str(scope or "").upper()
    if scope_key not in RESCUE_SCOPES:
        return None, ""

    headline = str(result.get("title", "") or "").strip()
    url = str(result.get("url", "") or "").strip()
    description = str(result.get("description", "") or "").strip()
    if not headline or not url or base._blocked_source(url):
        return None, ""
    if market._is_noise(headline, description):
        return None, ""

    combined = f"{headline} {description}"
    symbol = base._extract_symbol(combined, symbols)
    impact_hits = market._hit_count(combined, market.MARKET_IMPACT_TERMS)
    movement_hits = market._hit_count(combined, market.MOVEMENT_EVENT_TERMS)
    indonesia_relevant = _strict_indonesia_relevant(combined, symbol)
    quality_adjustment, factual_hits, speculative_hits = market._event_quality(
        headline, description
    )

    if scope_key == "INDONESIA" and not indonesia_relevant:
        return None, ""

    category, category_hits, fallback_used = _resolve_category(
        scope=scope_key,
        combined=combined,
        impact_hits=impact_hits,
        movement_hits=movement_hits,
        factual_hits=factual_hits,
        speculative_hits=speculative_hits,
        indonesia_relevant=indonesia_relevant,
    )
    if not category:
        return None, ""

    if not _resilient_source_allowed(
        url=url,
        scope=scope_key,
        category_hits=category_hits,
        indonesia_relevant=indonesia_relevant,
        symbol=symbol,
        impact_hits=impact_hits,
        movement_hits=movement_hits,
        factual_hits=factual_hits,
        speculative_hits=speculative_hits,
    ):
        return None, ""

    if scope_key == "GLOBAL":
        if impact_hits < 1:
            return None, ""
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
                return None, ""
        if speculative_hits >= 2 and factual_hits == 0:
            return None, ""

    tier_bonus = {"A": 1.5, "B": 0.6, "C": 0.0}.get(
        market._source_tier(url), 0.0
    )
    score = float(base._source_score(url)) + tier_bonus
    score += market.CATEGORY_PRIORITY.get(category, 0.0)
    score += min(3.0, float(impact_hits) * 0.30)
    score += min(2.0, float(movement_hits) * 0.35)
    score += min(1.5, float(category_hits) * 0.30)
    score += quality_adjustment
    score += 1.0 if len(headline) >= 30 else 0.0

    age = str(result.get("age", "") or "").strip()
    page_age = str(
        result.get("page_age", "") or result.get("published_at", "") or ""
    ).strip()
    reason = "no_category" if fallback_used else "source_policy"

    return (
        base.NewsItem(
            headline=headline,
            source=market._source_label(result),
            url=url,
            published_at=page_age,
            age=age,
            scope=scope_key,
            category=category,
            symbol="",
            score=score,
        ),
        reason,
    )


def _diagnose_market_rejection(
    result: dict[str, Any], *, scope: str, symbols: list[str]
) -> str:
    """Report the first failed gate after the resilient runtime policy."""
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
    indonesia_relevant = _strict_indonesia_relevant(combined, symbol)
    _, factual_hits, speculative_hits = market._event_quality(headline, description)

    if scope_key in LOCAL_SCOPES and not indonesia_relevant:
        return "indonesia_relevance"
    if scope_key == "ISSUER" and not symbol:
        return "issuer_symbol"

    category, category_hits, _ = _resolve_category(
        scope=scope_key,
        combined=combined,
        impact_hits=impact_hits,
        movement_hits=movement_hits,
        factual_hits=factual_hits,
        speculative_hits=speculative_hits,
        indonesia_relevant=indonesia_relevant,
    )
    if not category:
        return "no_category"

    if not _resilient_source_allowed(
        url=url,
        scope=scope_key,
        category_hits=category_hits,
        indonesia_relevant=indonesia_relevant,
        symbol=symbol,
        impact_hits=impact_hits,
        movement_hits=movement_hits,
        factual_hits=factual_hits,
        speculative_hits=speculative_hits,
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

    if (
        scope_key == "SECTOR"
        and movement_hits < 1
        and impact_hits < 1
        and category_hits < 2
    ):
        return "sector_relevance"

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
    scope_key = str(scope or "").upper()
    headline = str(result.get("title", "") or "").strip()
    description = str(result.get("description", "") or "").strip()
    combined = f"{headline} {description}"
    symbol = base._extract_symbol(combined, symbols)

    item = _ORIGINAL_NORMALIZE(result, scope=scope_key, symbols=symbols)

    # Tighten local-scope identity even when the baseline accepted generic macro
    # wording (e.g. a US bond-yield article returned under INDONESIA).
    if item is not None and scope_key in LOCAL_SCOPES:
        if not _strict_indonesia_relevant(combined, symbol):
            _record_rejection("indonesia_relevance", result, scope_key)
            return None

    if item is None:
        original_reason = _diagnose_market_rejection(
            result, scope=scope_key, symbols=symbols
        )
        rescued, rescue_reason = _resilient_normalize_result(
            result, scope=scope_key, symbols=symbols
        )
        if rescued is None:
            _record_rejection(original_reason, result, scope_key)
            return None
        item = rescued
        _record_rescue(rescue_reason or original_reason)

    if not _is_recent_item(item):
        _record_rejection("freshness", result, scope_key)
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
    enriched["rescued_results"] = sum(_RESCUE_COUNTS.values())
    enriched["rescue_counts"] = dict(sorted(_RESCUE_COUNTS.items()))
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
    market.market_query_plan = _focused_market_query_plan
    market._freshness_for_scope = _freshness_for_scope
    market._limit_market_items = _limit_market_items
    market.format_market_digest = format_market_digest


def main() -> int:
    install_policy()
    return market.main()


if __name__ == "__main__":
    raise SystemExit(main())
