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


def test_specific_final_watchlist_route_beats_legacy_signal_env() -> None:
    config = {
        "telegram_ui": {
            "topic_routing": {
                "final_watchlist_summary": "9",
                "final_watchlist_detail": "9",
                "final_watchlist_csv": "9",
                "signal_detail": "6",
            }
        }
    }
    router = TelegramRouter(config, {"TELEGRAM_THREAD_SIGNAL_ID": "999"})

    summary = router.resolve("final_watchlist_summary", "report")
    detail = router.resolve("final_watchlist_detail", "report")
    signal_detail = router.resolve("signal_detail", "report")

    assert summary.category == "SIGNAL"
    assert summary.message_thread_id == "9"
    assert detail.message_thread_id == "9"
    assert signal_detail.message_thread_id == "6"
    assert summary.fallback_to_main_chat is False
