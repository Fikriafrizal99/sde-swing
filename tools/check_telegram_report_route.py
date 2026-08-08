#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.runtime import load_environment_file
from modules.telegram.router import TelegramRouter


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def effective_thread(router: TelegramRouter, scheduler: dict, report_type: str, topic: str) -> str:
    route = router.resolve(report_type, topic)
    if route.message_thread_id:
        return str(route.message_thread_id).strip()
    routing = scheduler.get("delivery", {}).get("topic_routing", {})
    return str(routing.get(report_type) or routing.get(topic) or "").strip()


def telegram_credentials(telegram_cfg: dict) -> tuple[str, str]:
    runtime_cfg = telegram_cfg.get("telegram", {}) if isinstance(telegram_cfg.get("telegram", {}), dict) else {}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or str(runtime_cfg.get("bot_token", "") or "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or str(runtime_cfg.get("chat_id", "") or "").strip()
    return token, chat_id


def validate_live_topic(token: str, chat_id: str, thread_id: str, timeout: int = 12) -> tuple[bool, str]:
    """Validate a topic against the exact bot + chat used by delivery.

    sendChatAction is non-persistent but accepts message_thread_id, so Telegram
    itself becomes the source of truth for whether the topic exists in the
    configured forum chat.
    """
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum lengkap"
    if not str(thread_id).isdigit() or int(thread_id) <= 0:
        return False, f"thread_id tidak valid: {thread_id!r}"

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendChatAction",
            data={
                "chat_id": chat_id,
                "message_thread_id": str(thread_id),
                "action": "typing",
            },
            timeout=max(int(timeout), 1),
        )
    except requests.RequestException as exc:
        return False, f"Telegram API tidak dapat dihubungi: {type(exc).__name__}: {exc}"

    try:
        payload = response.json()
    except Exception:
        return False, f"Telegram API bukan JSON (HTTP {response.status_code})"

    if response.ok and payload.get("ok"):
        return True, "VALID"

    description = str(payload.get("description") or f"HTTP {response.status_code}")
    return False, description


def main() -> int:
    load_environment_file(ROOT / ".env")
    scheduler = read_json(ROOT / "config/scheduler.json")
    telegram_cfg = read_json(ROOT / "config/telegram.json")
    router = TelegramRouter(telegram_cfg, os.environ)

    report_thread = effective_thread(router, scheduler, "position_management", "report")
    market_thread = effective_thread(router, scheduler, "market_outlook", "market_outlook")
    post_thread = effective_thread(router, scheduler, "post_market", "post_market")

    if not report_thread:
        print("[FAILED] Topic Report belum dikonfigurasi.")
        print("Set TELEGRAM_THREAD_REPORT_ID ke message_thread_id topic Report.")
        print("Gunakan RUN_SDE.bat > Portfolio Operations > Configure / Cek Topic Telegram Report.")
        return 1

    conflicts = sorted({item for item in (market_thread, post_thread) if item and item == report_thread})
    if conflicts:
        print(f"[FAILED] Topic Report thread_id={report_thread} masih sama dengan Market/Post Market.")
        print("Set TELEGRAM_THREAD_REPORT_ID ke thread ID topic Report yang berbeda.")
        return 2

    token, chat_id = telegram_credentials(telegram_cfg)
    live_ok, live_detail = validate_live_topic(token, chat_id, report_thread)
    if not live_ok:
        print(f"[FAILED] Topic Report thread_id={report_thread} ditolak Telegram: {live_detail}")
        print("Thread ID harus berasal dari topic Report pada TELEGRAM_CHAT_ID yang sama dan topic tersebut masih aktif.")
        print("Kirim 'REPORT TEST' di topic Report, lalu gunakan menu konfigurasi untuk membaca ulang thread ID.")
        return 3

    print(
        f"[OK] Telegram route LIVE VALID: Report={report_thread} | "
        f"Market={market_thread or '-'} | PostMarket={post_thread or '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
