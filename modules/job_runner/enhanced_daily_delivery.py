from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from modules.telegram.router import TelegramRouter
from modules.job_runner.enhanced_daily_reports import DailyReportArtifact


class EnhancedDailyDelivery:
    """Telegram sender for agreed daily report messages and CSV attachments."""

    def __init__(self, telegram_config: dict[str, Any], timeout_seconds: int = 30) -> None:
        self.config = telegram_config
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.router = TelegramRouter(telegram_config, os.environ)

    @property
    def configured(self) -> bool:
        return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())

    def deliver(self, artifacts: list[DailyReportArtifact], dry_run: bool = False) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for artifact in artifacts:
            if dry_run:
                results.append(self._result(artifact, "DRY_RUN"))
                continue
            try:
                if artifact.attachment_path is not None:
                    response = self._send_document(artifact)
                else:
                    response = self._send_message(artifact)
                results.append(self._result(
                    artifact,
                    "SENT",
                    telegram_message_id=response.get("result", {}).get("message_id", ""),
                ))
            except Exception as exc:
                results.append(self._result(artifact, "FAILED", error=str(exc)))
        return results

    def _route(self, artifact: DailyReportArtifact) -> dict[str, Any]:
        route = self.router.resolve(artifact.report_type, artifact.topic)
        return route.to_dict()

    def _base_data(self, artifact: DailyReportArtifact) -> dict[str, Any]:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if requests is None:
            raise RuntimeError("Dependency requests belum terpasang")
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID belum dikonfigurasi")
        route = self._route(artifact)
        data: dict[str, Any] = {"chat_id": chat_id}
        thread_id = str(route.get("message_thread_id") or "").strip()
        if thread_id:
            data["message_thread_id"] = thread_id
        return data

    def _send_message(self, artifact: DailyReportArtifact) -> dict[str, Any]:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        data = self._base_data(artifact)
        data.update({
            "text": artifact.text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            timeout=self.timeout_seconds,
        )
        return self._validated(response)

    def _send_document(self, artifact: DailyReportArtifact) -> dict[str, Any]:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        path = Path(artifact.attachment_path or "")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Attachment tidak ditemukan: {path}")
        data = self._base_data(artifact)
        data.update({"caption": artifact.caption or artifact.text or path.name, "parse_mode": "HTML"})
        with path.open("rb") as handle:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data=data,
                files={"document": (path.name, handle, "text/csv")},
                timeout=self.timeout_seconds,
            )
        return self._validated(response)

    @staticmethod
    def _validated(response: Any) -> dict[str, Any]:
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError(f"Respons Telegram bukan JSON: HTTP {response.status_code}") from exc
        if not response.ok or not body.get("ok"):
            raise RuntimeError(f"Telegram API gagal: {body}")
        return body

    def _result(self, artifact: DailyReportArtifact, status: str, **extra: Any) -> dict[str, Any]:
        return {
            "report_type": artifact.report_type,
            "symbol": artifact.symbol,
            "attachment_path": str(artifact.attachment_path or ""),
            "status": status,
            **self._route(artifact),
            **extra,
        }
