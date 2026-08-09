from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from modules.news import news_monitor as base
from modules.news import news_monitor_market_impact as market


def _result(
    title: str,
    description: str = "market impact update",
    url: str = "https://www.reuters.com/markets/test-story",
) -> dict[str, object]:
    return {
        "title": title,
        "url": url,
        "description": description,
        "age": "1h",
    }


def test_market_impact_covers_all_14_event_categories() -> None:
    cases = [
        ("INDEX", "MSCI Indonesia adds ANTM to Global Standard Index in August review", "INDEX_REBALANCING"),
        ("CORPORATE", "IDX Indonesia issuer announces rights issue and private placement", "CORPORATE_ACTION"),
        ("CORPORATE", "IDX Indonesia issuer reports net profit surge and raises guidance", "EARNINGS_GUIDANCE"),
        ("CORPORATE", "IDX Indonesia issuer wins Rp4 trillion contract and expands order book", "CONTRACT_PROJECT"),
        ("CORPORATE", "IDX Indonesia issuer announces acquisition and new controlling shareholder", "MNA_OWNERSHIP"),
        ("INDONESIA", "Indonesia government raises nickel royalty under new regulation", "REGULATION_POLICY"),
        ("RISK", "IDX suspends trading in ABCD after unusual market activity UMA", "EXCHANGE_EVENT"),
        ("SECTOR", "Indonesia nickel prices surge after production cut supports mining stocks", "COMMODITY_CATALYST"),
        ("RISK", "Indonesia IDX issuer bond rating downgrade raises refinancing risk", "FUNDING_DEBT"),
        ("RISK", "Indonesia mine shutdown after fire causes production halt", "OPERATIONAL_EVENT"),
        ("INDEX", "Indonesia foreign passive fund inflow rises after large block trade in IDX shares", "FOREIGN_PASSIVE_FLOW"),
        ("CORPORATE", "Indonesia IDX issuer director resigns and discloses material transaction", "MANAGEMENT_DISCLOSURE"),
        ("INDONESIA", "Bank Indonesia holds BI Rate as rupiah and inflation remain stable", "MACRO_INDONESIA"),
        ("GLOBAL", "Fed CPI surprise lifts Treasury yields and dollar, pressuring stocks and oil", "GLOBAL_CATALYST"),
    ]

    for scope, headline, expected_category in cases:
        item = market.strict_normalize_result(
            _result(headline),
            scope=scope,
            symbols=["ANTM", "ABCD"],
        )
        assert item is not None, (scope, headline)
        assert item.category == expected_category


def test_msci_watchlist_story_is_kept_as_issuer_event() -> None:
    item = market.strict_normalize_result(
        _result("MSCI adds ANTM to Global Standard Index effective this month"),
        scope="ISSUER",
        symbols=["ANTM"],
    )
    assert item is not None
    assert item.symbol == "ANTM"
    assert item.category == "INDEX_REBALANCING"


def test_irrelevant_political_story_without_market_path_is_rejected() -> None:
    item = market.strict_normalize_result(
        _result("President discusses election strategy with parliament"),
        scope="GLOBAL",
        symbols=[],
    )
    assert item is None


def test_speculative_global_opinion_without_concrete_event_is_rejected() -> None:
    item = market.strict_normalize_result(
        _result("Is Trumpflation real? Why Wall Street fears this could be a reason for stock market crash"),
        scope="GLOBAL",
        symbols=[],
    )
    assert item is None


def test_factual_index_event_scores_above_speculative_index_story() -> None:
    factual = market.strict_normalize_result(
        _result("MSCI Indonesia officially adds ANTM to Global Standard Index effective August 31"),
        scope="INDEX",
        symbols=["ANTM"],
    )
    speculative = market.strict_normalize_result(
        _result("Could ANTM enter MSCI? Analysts say it may be included in the next review"),
        scope="INDEX",
        symbols=["ANTM"],
    )
    assert factual is not None
    assert speculative is not None
    assert factual.score > speculative.score


def test_low_quality_macro_sources_are_rejected() -> None:
    for url in (
        "https://cryptobriefing.com/us-payroll-drop-job-market-concerns-fed",
        "https://seekingalpha.com/article/test",
        "https://home.nzcity.co.nz/news/article.aspx?id=123",
    ):
        item = market.strict_normalize_result(
            _result(
                "Fed payroll surprise moves Treasury yields dollar and stocks",
                url=url,
            ),
            scope="GLOBAL",
            symbols=[],
        )
        assert item is None


def test_query_plan_has_seven_market_coverage_scopes() -> None:
    plans = market.market_query_plan(["ANTM", "BBRI"])
    scopes = {plan["scope"] for plan in plans}
    assert scopes == {"GLOBAL", "INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK", "ISSUER"}
    joined = " ".join(plan["query"].lower() for plan in plans)
    for required in (
        "msci", "ftse", "rights issue", "earnings", "contract", "acquisition",
        "royalty", "suspension", "nickel", "refinancing", "force majeure",
        "foreign flow", "material transaction", "bi rate", "federal reserve",
    ):
        assert required in joined


def test_material_scopes_get_longer_search_lookback_without_expanding_output_cap() -> None:
    current = datetime(2026, 8, 9, 21, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    config = {"post_market_freshness": "today"}

    assert market._freshness_for_scope("post_market", "GLOBAL", config, current) == "2026-08-09to2026-08-09"
    assert market._freshness_for_scope("post_market", "SECTOR", config, current) == "2026-08-09to2026-08-09"
    assert market._freshness_for_scope("post_market", "INDEX", config, current) == "2026-08-03to2026-08-09"
    assert market._freshness_for_scope("post_market", "CORPORATE", config, current) == "2026-08-05to2026-08-09"
    assert market._freshness_for_scope("post_market", "RISK", config, current) == "2026-08-07to2026-08-09"
    assert market._freshness_for_scope("post_market", "ISSUER", config, current) == "2026-08-07to2026-08-09"


def test_semantic_dedupe_collapses_rephrased_same_story() -> None:
    left = base.NewsItem(
        headline="Indonesians sound alarm over historic low in currency - ABC News",
        source="ABC News",
        url="https://abc.net.au/news/currency",
        published_at="",
        age="12h",
        scope="INDONESIA",
        category="MACRO_INDONESIA",
        score=10.0,
    )
    right = base.NewsItem(
        headline="'Total chaos': Indonesians sound alarm over historic low in currency",
        source="Another Source",
        url="https://example.com/syndicated-currency",
        published_at="",
        age="15h",
        scope="INDONESIA",
        category="MACRO_INDONESIA",
        score=8.0,
    )
    deduped = market._dedupe_market_items([left, right])
    assert len(deduped) == 1
    assert deduped[0].source == "ABC News"


def test_sent_history_suppresses_same_story_across_different_url() -> None:
    item = base.NewsItem(
        headline="MSCI adds ANTM to Global Standard Index effective this month",
        source="Reuters",
        url="https://reuters.com/new-msci-url",
        published_at="",
        age="1d",
        scope="ISSUER",
        category="INDEX_REBALANCING",
        symbol="ANTM",
        score=12.0,
    )
    seen = [{
        "url": "https://example.com/old-msci-url",
        "headline": "ANTM added to MSCI Global Standard Index effective this month",
        "category": "INDEX_REBALANCING",
        "symbol": "ANTM",
    }]
    assert market._already_sent(item, seen) is True


def test_new_limiter_preserves_new_scopes_and_hard_caps_ten() -> None:
    items = []
    for index in range(20):
        scope = market.DISPLAY_SCOPES[index % len(market.DISPLAY_SCOPES)]
        items.append(
            base.NewsItem(
                headline=f"{scope} material story {index}",
                source="Reuters",
                url=f"https://reuters.com/{scope.lower()}/{index}",
                published_at="",
                age="1h",
                scope=scope,
                category="GLOBAL_CATALYST" if scope == "GLOBAL" else "MACRO_INDONESIA",
                score=20.0 - index,
            )
        )
    selected = market._limit_market_items(items, 50)
    assert len(selected) == 10
    assert {item.scope for item in selected}.issuperset(set(market.DISPLAY_SCOPES))


def test_digest_groups_new_sections_and_keeps_news_informational_only() -> None:
    items = [
        base.NewsItem(
            headline="MSCI Indonesia review",
            source="Reuters",
            url="https://reuters.com/msci",
            published_at="",
            age="1h",
            scope="INDEX",
            category="INDEX_REBALANCING",
            score=10.0,
        ),
        base.NewsItem(
            headline="IDX issuer wins material contract",
            source="Reuters",
            url="https://reuters.com/contract",
            published_at="",
            age="1h",
            scope="CORPORATE",
            category="CONTRACT_PROJECT",
            score=9.0,
        ),
    ]
    rendered = market.format_market_digest("morning", items)
    assert "INDEX &amp; REBALANCING" in rendered
    assert "CORPORATE EVENTS" in rendered
    assert "Index / Rebalancing" in rendered
    assert "Contract / Project" in rendered
    assert "tidak memengaruhi keputusan SDE" in rendered
