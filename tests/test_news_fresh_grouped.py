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


def test_focused_queries_keep_local_scopes_explicitly_indonesia_anchored() -> None:
    plans = fresh._focused_market_query_plan(["ANTM", "BBRI"])
    by_scope = {plan["scope"]: plan["query"].lower() for plan in plans}
    assert set(by_scope) == {"GLOBAL", "INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK", "ISSUER"}
    for scope in ("INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK", "ISSUER"):
        assert "indonesia" in by_scope[scope]
    assert "ihsg" in by_scope["INDONESIA"]
    assert "bank indonesia" in by_scope["INDONESIA"]
    assert "msci indonesia" in by_scope["INDEX"]


def test_generic_us_macro_terms_do_not_count_as_indonesia_identity() -> None:
    text = "US Treasury yields rise as inflation surprises markets and bond prices fall"
    assert fresh._strict_indonesia_relevant(text) is False


def test_tier_c_global_story_with_strong_factual_market_evidence_is_rescued() -> None:
    result = {
        "title": "Government officially raises tariffs as stocks fall and dollar rises",
        "description": "The announced tariff increase pushed equities lower and bond yields higher.",
        "url": "https://examplejournal.com/global/tariff-move",
        "age": "2h",
    }
    fresh._reset_diagnostics()
    item = fresh.strict_normalize_recent(result, scope="GLOBAL", symbols=[])
    assert item is not None
    assert item.category == "GLOBAL_CATALYST"
    assert fresh._RESCUE_COUNTS == {"source_policy": 1}
    assert fresh._REJECTION_COUNTS == {}


def test_tier_c_speculative_global_story_remains_rejected() -> None:
    result = {
        "title": "Could tariffs crash stocks and push the dollar higher?",
        "description": "Analysts say markets may fall if a possible trade war escalates.",
        "url": "https://examplejournal.com/global/speculation",
        "age": "2h",
    }
    fresh._reset_diagnostics()
    item = fresh.strict_normalize_recent(result, scope="GLOBAL", symbols=[])
    assert item is None
    assert sum(fresh._REJECTION_COUNTS.values()) == 1


def test_strong_global_market_story_can_use_category_fallback() -> None:
    result = {
        "title": "Official auction result sends bond yields higher as equities fall",
        "description": "Currency markets moved after the official announcement and liquidity tightened.",
        "url": "https://examplejournal.com/global/auction",
        "age": "2h",
    }
    fresh._reset_diagnostics()
    item = fresh.strict_normalize_recent(result, scope="GLOBAL", symbols=[])
    assert item is not None
    assert item.category == "GLOBAL_CATALYST"
    assert fresh._RESCUE_COUNTS == {"no_category": 1}


def test_indonesia_scope_rejects_foreign_macro_story_even_if_baseline_terms_match() -> None:
    result = {
        "title": "Treasury yields rise as US inflation surprises markets",
        "description": "Federal Reserve rate expectations pushed bond yields and the dollar higher.",
        "url": "https://reuters.com/world/us-macro",
        "age": "2h",
    }
    fresh._reset_diagnostics()
    item = fresh.strict_normalize_recent(result, scope="INDONESIA", symbols=[])
    assert item is None
    assert fresh._REJECTION_COUNTS == {"indonesia_relevance": 1}


def test_issuer_symbol_requirement_is_never_relaxed() -> None:
    result = {
        "title": "Global stocks rally after official tariff agreement",
        "description": "Equities and the dollar moved after the announced trade deal.",
        "url": "https://reuters.com/markets/global-rally",
        "age": "2h",
    }
    fresh._reset_diagnostics()
    item = fresh.strict_normalize_recent(result, scope="ISSUER", symbols=["ANTM"])
    assert item is None
    assert fresh._REJECTION_COUNTS == {"issuer_symbol": 1}


def test_diagnostic_classifies_missing_event_category() -> None:
    result = {
        "title": "Stocks market morning update",
        "description": "Broad market conditions remain mixed today.",
        "url": "https://reuters.com/markets/morning-update",
        "age": "1h",
    }
    reason = fresh._diagnose_market_rejection(result, scope="GLOBAL", symbols=[])
    assert reason == "no_category"


def test_strict_recent_records_freshness_without_changing_baseline_acceptance() -> None:
    fresh._reset_diagnostics()
    accepted = fresh.strict_normalize_recent(_global_result(age="1h"), scope="GLOBAL", symbols=[])
    assert accepted is not None
    assert fresh._REJECTION_COUNTS == {}

    rejected = fresh.strict_normalize_recent(_global_result(age="25h"), scope="GLOBAL", symbols=[])
    assert rejected is None
    assert fresh._REJECTION_COUNTS == {"freshness": 1}
    assert fresh._REJECTION_SAMPLES[0]["reason"] == "freshness"


def test_collection_diagnostics_and_rescue_counts_are_added_to_meta(monkeypatch) -> None:
    rejected_result = {
        "title": "Stocks market morning update",
        "description": "Broad market conditions remain mixed today.",
        "url": "https://reuters.com/markets/morning-update",
        "age": "1h",
    }
    rescued_result = {
        "title": "Government officially raises tariffs as stocks fall and dollar rises",
        "description": "The announced tariff increase pushed equities lower and bond yields higher.",
        "url": "https://examplejournal.com/global/tariff-move",
        "age": "2h",
    }

    def fake_collect(session: str, scheduler=None):
        del scheduler
        assert session == "morning"
        assert fresh.strict_normalize_recent(rejected_result, scope="GLOBAL", symbols=[]) is None
        rescued = fresh.strict_normalize_recent(rescued_result, scope="GLOBAL", symbols=[])
        assert rescued is not None
        return [rescued], {
            "session": session,
            "raw_results": 2,
            "normalized_results": 1,
        }

    monkeypatch.setattr(fresh, "_ORIGINAL_COLLECT", fake_collect)
    items, meta = fresh.collect_market_news_with_diagnostics("morning")

    assert len(items) == 1
    assert meta["rejected_results"] == 1
    assert meta["rejection_counts"] == {"no_category": 1}
    assert meta["rejection_samples"][0]["reason"] == "no_category"
    assert meta["rejection_samples"][0]["scope"] == "GLOBAL"
    assert meta["rescued_results"] == 1
    assert meta["rescue_counts"] == {"source_policy": 1}
