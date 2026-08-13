from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from modules.news.news_monitor import NewsItem
from modules.news.news_monitor_market_impact import (
    _source_label,
    format_market_digest,
    strict_normalize_result,
)


def test_history_article_is_rejected_even_if_it_mentions_market() -> None:
    result = {
        "title": "Sejarah Pasar Modal RI: Era Kolonial hingga Kini Berusia 49 Tahun",
        "description": "Membahas perjalanan pasar modal Indonesia dan IHSG.",
        "url": "https://market.bisnis.com/read/example",
        "age": "1 day ago",
    }
    assert strict_normalize_result(result, scope="INDONESIA", symbols=[]) is None


def test_political_story_without_explicit_market_link_is_rejected() -> None:
    result = {
        "title": "Donald Trump moves to punish Russia, India and China",
        "description": "Senate political debate intensifies over Russia policy.",
        "url": "https://timesofindia.indiatimes.com/world/example",
        "age": "2 hours ago",
    }
    assert strict_normalize_result(result, scope="GLOBAL", symbols=[]) is None


def test_strong_market_move_is_kept() -> None:
    result = {
        "title": "Gold prices jump 7% as dollar falls and yields retreat",
        "description": "Gold posts its strongest weekly gain as Treasury yields and the dollar weaken.",
        "url": "https://economictimes.indiatimes.com/markets/commodities/example",
        "age": "2 hours ago",
    }
    item = strict_normalize_result(result, scope="GLOBAL", symbols=[])
    assert item is not None
    assert item.source == "The Economic Times"


def test_bisnis_section_name_is_normalized_to_publisher() -> None:
    result = {
        "url": "https://market.bisnis.com/read/20260809/7/example",
        "profile": {"name": "Market"},
    }
    assert _source_label(result) == "Bisnis.com"


def test_telegram_format_bolds_title_sections_and_headline() -> None:
    item = NewsItem(
        headline="Oil & gold rise as yields fall",
        source="Reuters",
        url="https://reuters.com/example?a=1&b=2",
        published_at="",
        age="1 hour ago",
        scope="GLOBAL",
        category="GLOBAL",
        score=8,
    )
    text = format_market_digest(
        "morning",
        [item],
        datetime(2026, 8, 9, 13, 27, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert "📰 <b>SDE SWING — MORNING NEWS</b>" in text
    assert "🧪 <b>Manual / Off-Hours News Run</b>" in text
    assert "🌍 <b>GLOBAL &amp; MACRO</b>" in text
    assert "◆ <b>Oil &amp; gold rise as yields fall</b>" in text
    assert "📊 <b>1 berita berdampak terhadap market</b>" in text
    assert "https://reuters.com/example?a=1&amp;b=2" in text
