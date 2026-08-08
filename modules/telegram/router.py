from __future__ import annotations

"""Telegram routing with category defaults and explicit report-topic isolation."""

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
    SIGNAL_TYPES = {
        "final_watchlist",
        "final_watchlist_summary",
        "final_watchlist_detail",
        "final_watchlist_csv",
        "final_decision",
        "signal",
        "signal_detail",
    }
    SYSTEM_TYPES = {"system", "data_warning", "startup", "source_health", "config_error", "dependency_failure"}

    def __init__(self, config: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> None:
        self.config = dict(config or {})
        self.environ = os.environ if environ is None else environ

    def category_for(self, report_type: str, topic: str = "") -> str:
        report = str(report_type or "").strip().lower()
        label = str(topic or "").strip().lower()
        if report in self.SIGNAL_TYPES or label in self.SIGNAL_TYPES:
            return "SIGNAL"
        if report in self.SYSTEM_TYPES or label in self.SYSTEM_TYPES:
            return "SYSTEM"
        return "REPORT"

    def resolve(self, report_type: str, topic: str = "") -> TelegramRoute:
        report = str(report_type or "").strip()
        label = str(topic or "").strip()
        report_lower = report.lower()
        label_lower = label.lower()
        category = self.category_for(report, label)
        ui = self.config.get("telegram_ui", {}) if isinstance(self.config, dict) else {}
        ui_routing = ui.get("topic_routing", {}) if isinstance(ui, dict) else {}

        # Per-report/per-topic configuration is always safe and specific.
        specific_config = (
            ui_routing.get(report)
            or ui_routing.get(report_lower)
            or ui_routing.get(label)
            or ui_routing.get(label_lower)
            or ""
        )

        env_name = f"TELEGRAM_THREAD_{category}_ID"
        env_thread = str(self.environ.get(env_name, "") or "").strip()
        category_config = str(ui_routing.get(category) or ui_routing.get(category.lower()) or "").strip()

        if category == "REPORT" and label_lower not in {"report", "reports"}:
            # Market Outlook, Post Market, broker reports, evaluation, etc. are
            # also REPORT-category payloads, but they have their own routes in
            # scheduler/config. A generic TELEGRAM_THREAD_REPORT_ID must not
            # hijack those routes. Return fallback when no specific UI mapping
            # exists so delivery.py can apply its per-report scheduler mapping.
            thread = str(specific_config).strip()
        else:
            # Explicit topic="report" is the dedicated general Report topic.
            # Environment remains first so local deployment can configure the
            # real Telegram message_thread_id without committing it to Git.
            thread = env_thread or str(specific_config).strip() or category_config

        return TelegramRoute(
            category=category,
            target_thread=category,
            message_thread_id=thread,
            fallback_to_main_chat=not bool(thread),
        )

    def credentials_configured(self) -> bool:
        return bool(str(self.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip() and str(self.environ.get("TELEGRAM_CHAT_ID", "") or "").strip())
