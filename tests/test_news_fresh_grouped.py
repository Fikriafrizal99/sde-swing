from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from modules.news import news_monitor as base
from modules.news import news_monitor_fresh_grouped as fresh


def _item(
    *,
    age: str = "1h",
    score: float = 1.0,
    category: str = "GLOBAL_CATALYST",
    headline: str = "Market update",
) -> base.NewsItem:
    return base.NewsItem(
        headline=headline,
        source="Reuters",
        url=f"https://reuters.com/{headline.lower().replace(' ', '-')}",
        published_at="",
        age=age,
        scope="GLOBAL",
        category=category,
        score=score,
    )


def _global_result(*, age: str = "1h") -> dict[str, str]:
    return {
        "title": "Federal Reserve holds rates as stocks rally",
        "description": "The Fed held rates while Wall Street stocks rallied after the announcement.",
        "url": "https://reuters.com/markets/fed-holds-rates",
        "age": age,
        "page_age": "",
    }


def test_recent_policy_rejects_articles_older_than_24_hours() -> None:
    current = datetime(2026, 8, 16, 19, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    assert fresh._is_recent_item(_item(age="23h"), current) is True
    assert fresh._is_recent_item(_item(age="24h"), current) is True
    assert fresh._is_recent_item(_item(age="25h"), current) is False
    assert fresh._is_recent_item(_item(age="2 days ago"), current) is False


def test_recent_policy_rejects_unknown_age() -> None:
    current = datetime(2026, 8, 16, 19, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    assert fresh._is_recent_item(_item(age=""), current) is False


def test_all_scopes_use_short_provider_freshness() -> None:
    current = datetime(2026, 8, 16, 19, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    config = {"post_market_freshness": "today", "morning_freshness": "pd"}
    for scope in ("GLOBAL", "INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK", "ISSUER"):
        assert fresh._freshness_for_scope("post_market", scope, config, current) == "2026-08-16to2026-08-16"
        assert fresh._freshness_for_scope("morning", scope, config, current) == "pd"


def test_limiter_keeps_all_surviving_items_without_five_or_ten_item_cap() -> None:
    items = [_item(score=float(index), headline=f"Story {index}") for index in range(17)]
    selected = fresh._limit_market_items(items, 5)
    assert len(selected) == 17
    assert selected[0].score == 16.0
    assert selected[-1].score == 0.0


def test_category_label_is_rendered_once_for_multiple_articles() -> None:
    items = [
        _item(headline="Fed holds rates", category="GLOBAL_CATALYST", score=2.0),
        _item(headline="Dollar falls after Fed", category="GLOBAL_CATALYST", score=1.0),
    ]
    rendered = fresh.format_market_digest(
        "morning",
        items,
        datetime(2026, 8, 16, 7, 30, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert rendered.count("🏷 <b>Global Catalyst</b>") == 1
    assert "Fed holds rates" in rendered
    assert "Dollar falls after Fed" in rendered
    assert "tidak memengaruhi keputusan SDE" in rendered


def test_diagnostic_classifies_missing_event_category() -> None:
    result = {
        "title": "Stocks market morning update",
        "description": "Broad market conditions remain mixed today.",
        "url": "https://reuters.com/markets/morning-update",
        "age": "1h",
    }
    reason = fresh._diagnose_market_rejection(result, scope="GLOBAL", symbols=[])
    assert reason == "no_category"


def test_strict_recent_records_freshness_without_changing_filter_contract() -> None:
    fresh._reset_diagnostics()
    accepted = fresh.strict_normalize_recent(_global_result(age="1h"), scope="GLOBAL", symbols=[])
    assert accepted is not None
    assert fresh._REJECTION_COUNTS == {}

    rejected = fresh.strict_normalize_recent(_global_result(age="25h"), scope="GLOBAL", symbols=[])
    assert rejected is None
    assert fresh._REJECTION_COUNTS == {"freshness": 1}
    assert fresh._REJECTION_SAMPLES[0]["reason"] == "freshness"


def test_collection_diagnostics_are_added_to_meta(monkeypatch) -> None:
    rejected_result = {
        "title": "Stocks market morning update",
        "description": "Broad market conditions remain mixed today.",
        "url": "https://reuters.com/markets/morning-update",
        "age": "1h",
    }

    def fake_collect(session: str, scheduler=None):
        del scheduler
        assert session == "morning"
        assert fresh.strict_normalize_recent(rejected_result, scope="GLOBAL", symbols=[]) is None
        return [], {
            "session": session,
            "raw_results": 1,
            "normalized_results": 0,
        }

    monkeypatch.setattr(fresh, "_ORIGINAL_COLLECT", fake_collect)
    items, meta = fresh.collect_market_news_with_diagnostics("morning")

    assert items == []
    assert meta["rejected_results"] == 1
    assert meta["rejection_counts"] == {"no_category": 1}
    assert meta["rejection_samples"][0]["reason"] == "no_category"
    assert meta["rejection_samples"][0]["scope"] == "GLOBAL"
