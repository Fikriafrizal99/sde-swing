from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from modules.news.news_monitor import NewsItem, dedupe_items, format_digest


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
