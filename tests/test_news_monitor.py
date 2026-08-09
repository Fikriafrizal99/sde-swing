from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from modules.news.news_monitor import (
    NewsItem,
    _resolve_freshness,
    dedupe_items,
    format_digest,
    query_plan,
)


def test_news_digest_omits_empty_sections_and_keeps_links() -> None:
    items = [
        NewsItem(
            headline="Federal Reserve signals data-dependent policy",
            source="Reuters",
            url="https://reuters.com/example",
            published_at="",
            age="2 hours ago",
            scope="GLOBAL",
            category="GLOBAL",
            score=5,
        ),
        NewsItem(
            headline="ENRG announces operational update",
            source="IDX",
            url="https://idx.co.id/example",
            published_at="",
            age="1 hour ago",
            scope="ISSUER",
            category="ISSUER",
            symbol="ENRG",
            score=6,
        ),
    ]
    text = format_digest("morning", items, datetime(2026, 8, 10, 7, 30, tzinfo=ZoneInfo("Asia/Jakarta")))
    assert "🌍 GLOBAL" in text
    assert "📌 EMITEN" in text
    assert "ENRG" in text
    assert "🔗 Baca: https://reuters.com/example" in text
    assert "🇮🇩 INDONESIA" not in text
    assert "🏭 SECTOR" not in text
    assert "tidak ada berita" not in text.lower()


def test_news_dedup_collapses_similar_headlines() -> None:
    items = [
        NewsItem("Oil prices rise as supply tightens", "Reuters", "https://reuters.com/a", "", "", "GLOBAL", "GLOBAL", score=5),
        NewsItem("Oil price rises as supply tightens", "Other", "https://example.com/b", "", "", "GLOBAL", "GLOBAL", score=1),
    ]
    assert len(dedupe_items(items)) == 1


def test_query_plan_localizes_with_query_text_not_country_parameter() -> None:
    plans = query_plan(["BBCA", "ENRG"])
    assert len(plans) == 4
    assert all("country" not in plan for plan in plans)
    assert all("search_lang" not in plan for plan in plans)
    indonesia = next(plan for plan in plans if plan["scope"] == "INDONESIA")
    assert "Indonesia" in indonesia["query"]
    issuer = next(plan for plan in plans if plan["scope"] == "ISSUER")
    assert "BBCA" in issuer["query"]
    assert "ENRG" in issuer["query"]


def test_post_market_today_is_converted_to_valid_brave_date_range() -> None:
    current = datetime(2026, 8, 10, 17, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    freshness = _resolve_freshness("post_market", {"post_market_freshness": "today"}, current)
    assert freshness == "2026-08-10to2026-08-10"
