"""Telegram delivery adapter for IDX disclosures using the existing NEWS topic."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import requests

from .models import IDXDisclosure


class TelegramDeliveryError(RuntimeError):
    """Raised when the existing Telegram NEWS route cannot deliver a message."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise TelegramDeliveryError(f"Cannot read Telegram config {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def resolve_news_topic_id(
    *,
    scheduler_config: Mapping[str, Any],
    telegram_config: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the existing NEWS topic without ever falling back to main chat."""
    env = os.environ if environ is None else environ
    candidates: list[Any] = [env.get("TELEGRAM_THREAD_NEWS_ID", "")]

    ui = (telegram_config or {}).get("telegram_ui", {})
    if isinstance(ui, Mapping):
        routing = ui.get("topic_routing", {})
        if isinstance(routing, Mapping):
            candidates.extend(
                [
                    routing.get("NEWS"),
                    routing.get("news"),
                    routing.get("morning_news"),
                    routing.get("post_market_news"),
                ]
            )

    delivery = scheduler_config.get("delivery", {})
    if isinstance(delivery, Mapping):
        routing = delivery.get("topic_routing", {})
        if isinstance(routing, Mapping):
            candidates.extend(
                [
                    routing.get("news"),
                    routing.get("morning_news"),
                    routing.get("post_market_news"),
                ]
            )

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text.isdigit() and int(text) > 0:
            return text

    raise TelegramDeliveryError(
        "Telegram NEWS topic tidak terkonfigurasi; main-chat fallback ditolak."
    )


class TelegramNewsDelivery:
    """Send IDX disclosure messages to the exact existing NEWS Telegram topic."""

    def __init__(
        self,
        *,
        scheduler_config_path: str | Path = "config/scheduler.json",
        telegram_config_path: str | Path = "config/telegram.json",
        timeout_seconds: float = 30,
        session: Any | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.scheduler_config_path = Path(scheduler_config_path)
        self.telegram_config_path = Path(telegram_config_path)
        self.timeout_seconds = float(timeout_seconds)
        self.session = session or requests.Session()
        self.environ = os.environ if environ is None else environ

        scheduler_config = _read_json(self.scheduler_config_path)
        telegram_config = _read_json(self.telegram_config_path)
        self.news_topic_id = resolve_news_topic_id(
            scheduler_config=scheduler_config,
            telegram_config=telegram_config,
            environ=self.environ,
        )

        telegram_section = telegram_config.get("telegram", {})
        if not isinstance(telegram_section, Mapping):
            telegram_section = {}

        self.bot_token = (
            str(self.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
            or str(telegram_section.get("bot_token", "") or "").strip()
        )
        self.chat_id = (
            str(self.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
            or str(telegram_section.get("chat_id", "") or "").strip()
        )
        if not self.bot_token or not self.chat_id:
            raise TelegramDeliveryError(
                "Telegram token/chat_id belum dikonfigurasi untuk IDX watcher."
            )

    def send(self, disclosure: IDXDisclosure, text: str) -> None:
        """Send exactly one factual IDX notification to the existing NEWS topic."""
        data = {
            "chat_id": self.chat_id,
            "message_thread_id": self.news_topic_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                data=data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TelegramDeliveryError(f"Telegram request failed: {exc}") from exc

        try:
            body = response.json()
        except Exception as exc:
            raise TelegramDeliveryError(
                f"Telegram response bukan JSON: HTTP {getattr(response, 'status_code', 0)}"
            ) from exc

        if not getattr(response, "ok", False) or not bool(body.get("ok")):
            raise TelegramDeliveryError(f"Telegram API gagal: {body}")
