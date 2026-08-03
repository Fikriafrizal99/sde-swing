from __future__ import annotations

"""Three-topic Telegram routing with environment-only credentials."""

from dataclasses import dataclass
import os
from typing import Any, Mapping


@dataclass(frozen=True)
class TelegramRoute:
    category: str
    target_thread: str
    message_thread_id: str
    fallback_to_main_chat: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "target_thread": self.target_thread,
            "message_thread_id": self.message_thread_id,
            "fallback_to_main_chat": self.fallback_to_main_chat,
        }


class TelegramRouter:
    SIGNAL_TYPES = {"final_watchlist", "final_decision", "signal", "signal_detail"}
    SYSTEM_TYPES = {"system", "data_warning", "startup", "source_health", "config_error", "dependency_failure"}

    def __init__(self, config: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> None:
        self.config = dict(config or {})
        self.environ = environ or os.environ

    def category_for(self, report_type: str, topic: str = "") -> str:
        report = str(report_type or "").strip().lower()
        label = str(topic or "").strip().lower()
        if report in self.SIGNAL_TYPES or label in self.SIGNAL_TYPES:
            return "SIGNAL"
        if report in self.SYSTEM_TYPES or label in self.SYSTEM_TYPES:
            return "SYSTEM"
        return "REPORT"

    def resolve(self, report_type: str, topic: str = "") -> TelegramRoute:
        category = self.category_for(report_type, topic)
        env_name = f"TELEGRAM_THREAD_{category}_ID"
        env_thread = str(self.environ.get(env_name, "") or "").strip()
        ui = self.config.get("telegram_ui", {}) if isinstance(self.config, dict) else {}
        ui_routing = ui.get("topic_routing", {}) if isinstance(ui, dict) else {}
        configured = ui_routing.get(report_type) or ui_routing.get(topic) or ui_routing.get(category) or ""
        thread = env_thread or str(configured).strip()
        return TelegramRoute(
            category=category,
            target_thread=category,
            message_thread_id=thread,
            fallback_to_main_chat=not bool(thread),
        )

    def credentials_configured(self) -> bool:
        return bool(str(self.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip() and str(self.environ.get("TELEGRAM_CHAT_ID", "") or "").strip())

