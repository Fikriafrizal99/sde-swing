from __future__ import annotations

from modules.news import news_monitor as base
from modules.news import news_monitor_market_impact as market


def _result(title: str, description: str = "market impact update") -> dict[str, object]:
    return {
        "title": title,
        "url": "https://www.reuters.com/markets/test-story",
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


def test_new_limiter_preserves_new_scopes() -> None:
    items = [
        base.NewsItem(
            headline=f"{scope} story",
            source="Reuters",
            url=f"https://reuters.com/{scope.lower()}",
            published_at="",
            age="1h",
            scope=scope,
            category="GLOBAL_CATALYST" if scope == "GLOBAL" else "MACRO_INDONESIA",
            score=10.0 - index,
        )
        for index, scope in enumerate(market.DISPLAY_SCOPES)
    ]
    selected = market._limit_market_items(items, 10)
    assert {item.scope for item in selected} == set(market.DISPLAY_SCOPES)


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
