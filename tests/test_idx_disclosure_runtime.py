from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from modules.idx_disclosure.telegram_delivery import (
    TelegramDeliveryError,
    resolve_news_topic_id,
)
from run_idx_disclosure_watcher import poll_interval_seconds


JAKARTA = ZoneInfo("Asia/Jakarta")


def test_poll_interval_uses_market_evening_and_overnight_windows():
    cfg = {
        "polling": {
            "market_window": {"start": "08:00", "end": "17:00", "interval_seconds": 60},
            "evening_window": {"start": "17:00", "end": "22:00", "interval_seconds": 180},
            "overnight_interval_seconds": 600,
        }
    }
    assert poll_interval_seconds(datetime(2026, 8, 20, 11, 0, tzinfo=JAKARTA), cfg) == 60
    assert poll_interval_seconds(datetime(2026, 8, 20, 18, 0, tzinfo=JAKARTA), cfg) == 180
    assert poll_interval_seconds(datetime(2026, 8, 20, 23, 0, tzinfo=JAKARTA), cfg) == 600


def test_news_topic_prefers_explicit_news_env_route():
    scheduler = {"delivery": {"topic_routing": {"news": "1451"}}}
    topic = resolve_news_topic_id(
        scheduler_config=scheduler,
        telegram_config={},
        environ={"TELEGRAM_THREAD_NEWS_ID": "999"},
    )
    assert topic == "999"


def test_news_topic_falls_back_to_existing_scheduler_news_route():
    scheduler = {"delivery": {"topic_routing": {"news": "1451"}}}
    topic = resolve_news_topic_id(
        scheduler_config=scheduler,
        telegram_config={},
        environ={},
    )
    assert topic == "1451"


def test_news_topic_never_falls_back_to_main_chat():
    with pytest.raises(TelegramDeliveryError):
        resolve_news_topic_id(
            scheduler_config={"delivery": {"topic_routing": {}}},
            telegram_config={},
            environ={},
        )
