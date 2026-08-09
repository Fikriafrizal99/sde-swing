from __future__ import annotations

from modules.telegram.router import TelegramRouter


def test_category_threads_use_env_for_signal_system_and_generic_report_only() -> None:
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
    assert router.resolve("report", "report").to_dict()["message_thread_id"] == "22"
    assert router.resolve("data_warning", "system").to_dict()["message_thread_id"] == "33"


def test_generic_report_env_does_not_hijack_concrete_report_types_even_with_legacy_report_label() -> None:
    env = {"TELEGRAM_THREAD_REPORT_ID": "22"}
    router = TelegramRouter({}, env)

    for report_type in ("market_outlook", "post_market", "position_management", "broker_summary"):
        route = router.resolve(report_type, "report")
        assert route.message_thread_id == ""
        assert route.fallback_to_main_chat is True


def test_specific_ui_route_wins_for_market_and_post_market_payloads() -> None:
    config = {
        "telegram_ui": {
            "topic_routing": {
                "market_outlook": "9",
                "post_market": "9",
            }
        }
    }
    router = TelegramRouter(config, {"TELEGRAM_THREAD_REPORT_ID": "22"})

    # Enhanced report payloads still carry topic="report". The concrete
    # report_type must therefore win over the generic REPORT environment route.
    assert router.resolve("market_outlook", "report").message_thread_id == "9"
    assert router.resolve("post_market", "report").message_thread_id == "9"


def test_position_management_is_left_for_scheduler_generic_report_fallback() -> None:
    router = TelegramRouter({}, {"TELEGRAM_THREAD_REPORT_ID": "22"})
    route = router.resolve("position_management", "report")

    # delivery.telegram_route() will then resolve scheduler topic_routing:
    # position_management (if configured) -> report. Current scheduler report
    # route is the portfolio/report topic, while Market/Post use dedicated IDs.
    assert route.message_thread_id == ""
    assert route.fallback_to_main_chat is True


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
