from __future__ import annotations

from modules.telegram.router import TelegramRouter


def test_news_has_dedicated_category_and_env_route() -> None:
    router = TelegramRouter({}, {"TELEGRAM_THREAD_NEWS_ID": "1451"})
    route = router.resolve("morning_news", "news")
    assert route.category == "NEWS"
    assert route.message_thread_id == "1451"
    assert route.fallback_to_main_chat is False


def test_news_without_topic_is_explicitly_unresolved() -> None:
    route = TelegramRouter({}, {}).resolve("post_market_news", "news")
    assert route.category == "NEWS"
    assert route.message_thread_id == ""
    assert route.fallback_to_main_chat is True


def test_report_isolation_is_unchanged() -> None:
    router = TelegramRouter({}, {"TELEGRAM_THREAD_REPORT_ID": "701"})
    assert router.resolve("market_outlook", "market_outlook").message_thread_id == ""
    # Concrete reports never inherit the generic REPORT env route directly.
    # delivery.telegram_route() applies scheduler-specific report fallback.
    assert router.resolve("position_management", "report").message_thread_id == ""
