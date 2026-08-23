from __future__ import annotations

"""Telegram routing with category defaults and explicit report/news isolation."""

from dataclasses import dataclass
import os
from typing import Any, Mapping


def _topic_id(value: Any) -> str:
    """Return a valid positive Telegram message_thread_id or an empty string."""
    text = str(value or "").strip()
    if not text.isdigit():
        return ""
    try:
        return text if int(text) > 0 else ""
    except ValueError:
        return ""


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
    NEWS_TYPES = {"news", "morning_news", "post_market_news"}

    def __init__(self, config: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> None:
        self.config = dict(config or {})
        self.environ = os.environ if environ is None else environ

    def category_for(self, report_type: str, topic: str = "") -> str:
        report = str(report_type or "").strip().lower()
        label = str(topic or "").strip().lower()
        if report in self.NEWS_TYPES or label in self.NEWS_TYPES or label == "news":
            return "NEWS"
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

        # Per-report/per-topic configuration is safe only when it is an actual
        # numeric Telegram topic ID. Ignore legacy symbolic labels.
        specific_config = ""
        for candidate in (
            ui_routing.get(report),
            ui_routing.get(report_lower),
            ui_routing.get(label),
            ui_routing.get(label_lower),
        ):
            normalized = _topic_id(candidate)
            if normalized:
                specific_config = normalized
                break

        env_name = f"TELEGRAM_THREAD_{category}_ID"
        env_thread = _topic_id(self.environ.get(env_name, ""))
        category_config = _topic_id(ui_routing.get(category) or ui_routing.get(category.lower()) or "")

        if category == "REPORT" and report_lower not in {"report", "reports"}:
            # A concrete report_type owns its dedicated route even when the
            # payload carries the legacy/generic topic label "report". This
            # prevents TELEGRAM_THREAD_REPORT_ID from hijacking Market Outlook,
            # Post Market, broker reports, etc. If no dedicated route exists,
            # delivery.py may still apply the scheduler fallback for topic=report.
            thread = specific_config
        elif category == "SIGNAL" and report_lower not in {"signal", "signals"}:
            # Concrete signal reports own their explicit per-report route.
            # TELEGRAM_THREAD_SIGNAL_ID is retained only as a compatibility
            # fallback and must never override Final Watchlist/Signal Detail
            # routes configured by the current application.
            thread = specific_config or env_thread or category_config
        elif category == "NEWS":
            # NEWS is intentionally isolated. Its caller must refuse main-chat
            # fallback when no numeric News topic exists.
            thread = env_thread or specific_config or category_config
        else:
            thread = env_thread or specific_config or category_config

        return TelegramRoute(
            category=category,
            target_thread=category,
            message_thread_id=thread,
            fallback_to_main_chat=not bool(thread),
        )

    def credentials_configured(self) -> bool:
        return bool(str(self.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip() and str(self.environ.get("TELEGRAM_CHAT_ID", "") or "").strip())
