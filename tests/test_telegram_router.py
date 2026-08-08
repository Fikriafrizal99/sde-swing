from __future__ import annotations

from modules.telegram.router import TelegramRouter


def test_category_threads_use_env_for_signal_system_and_explicit_report_topic() -> None:
    env = {
        "TELEGRAM_THREAD_SIGNAL_ID": "11",
        "TELEGRAM_THREAD_REPORT_ID": "22",
        "TELEGRAM_THREAD_SYSTEM_ID": "33",
    }
    router = TelegramRouter({}, env)
    assert router.resolve("final_watchlist", "final_watchlist").to_dict()["message_thread_id"] == "11"
    assert router.resolve("final_watchlist_summary", "report").to_dict()["message_thread_id"] == "11"
    assert router.resolve("final_watchlist_detail", "final_watchlist_detail").to_dict()["message_thread_id"] == "11"
    assert router.resolve("final_watchlist_csv", "final_watchlist_csv").to_dict()["message_thread_id"] == "11"
    assert router.resolve("position_management", "report").to_dict()["message_thread_id"] == "22"
    assert router.resolve("data_warning", "system").to_dict()["message_thread_id"] == "33"


def test_generic_report_env_does_not_hijack_market_or_post_market_routes() -> None:
    env = {"TELEGRAM_THREAD_REPORT_ID": "22"}
    router = TelegramRouter({}, env)

    market = router.resolve("market_outlook", "market_outlook")
    post = router.resolve("post_market", "post_market")

    assert market.message_thread_id == ""
    assert market.fallback_to_main_chat is True
    assert post.message_thread_id == ""
    assert post.fallback_to_main_chat is True


def test_specific_ui_route_still_wins_for_market_payload() -> None:
    config = {
        "telegram_ui": {
            "topic_routing": {
                "market_outlook": "9",
                "post_market": "9",
            }
        }
    }
    router = TelegramRouter(config, {"TELEGRAM_THREAD_REPORT_ID": "22"})
    assert router.resolve("market_outlook", "market_outlook").message_thread_id == "9"
    assert router.resolve("post_market", "post_market").message_thread_id == "9"


def test_final_watchlist_variants_are_signal_category() -> None:
    router = TelegramRouter({}, {})
    for report_type in (
        "final_watchlist",
        "final_watchlist_summary",
        "final_watchlist_detail",
        "final_watchlist_csv",
    ):
        assert router.category_for(report_type, "report") == "SIGNAL"


def test_empty_thread_falls_back_to_main_chat_without_marking_error() -> None:
    route = TelegramRouter({}, {}).resolve("position_management", "report")
    assert route.fallback_to_main_chat is True
    assert route.message_thread_id == ""
