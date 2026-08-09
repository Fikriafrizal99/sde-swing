from modules.telegram.router import TelegramRouter


def _config() -> dict:
    return {
        "telegram_ui": {
            "topic_routing": {
                "market_outlook": "9",
                "post_market": "9",
                "REPORT": "701",
            }
        }
    }


def test_market_outlook_uses_dedicated_topic_even_with_generic_report_label() -> None:
    router = TelegramRouter(_config(), {"TELEGRAM_THREAD_REPORT_ID": "701"})
    route = router.resolve("market_outlook", "report")
    assert route.category == "REPORT"
    assert route.message_thread_id == "9"
    assert route.fallback_to_main_chat is False


def test_post_market_uses_dedicated_topic_even_with_generic_report_label() -> None:
    router = TelegramRouter(_config(), {"TELEGRAM_THREAD_REPORT_ID": "701"})
    route = router.resolve("post_market", "report")
    assert route.category == "REPORT"
    assert route.message_thread_id == "9"
    assert route.fallback_to_main_chat is False


def test_position_management_without_dedicated_route_can_fall_back_to_scheduler_report_topic() -> None:
    router = TelegramRouter(_config(), {"TELEGRAM_THREAD_REPORT_ID": "701"})
    route = router.resolve("POSITION_MANAGEMENT", "report")
    assert route.category == "REPORT"
    assert route.message_thread_id == ""
    assert route.fallback_to_main_chat is True
