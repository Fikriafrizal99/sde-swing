from __future__ import annotations

from modules.telegram.router import TelegramRouter


def test_three_topic_routing_uses_env_thread_ids() -> None:
    env = {
        "TELEGRAM_THREAD_SIGNAL_ID": "11",
        "TELEGRAM_THREAD_REPORT_ID": "22",
        "TELEGRAM_THREAD_SYSTEM_ID": "33",
    }
    router = TelegramRouter({}, env)
    assert router.resolve("final_watchlist", "final_watchlist").to_dict()["message_thread_id"] == "11"
    assert router.resolve("market_outlook", "market_outlook").to_dict()["message_thread_id"] == "22"
    assert router.resolve("data_warning", "system").to_dict()["message_thread_id"] == "33"


def test_empty_thread_falls_back_to_main_chat_without_marking_error() -> None:
    route = TelegramRouter({}, {}).resolve("market_outlook", "REPORT")
    assert route.fallback_to_main_chat is True
    assert route.message_thread_id == ""

