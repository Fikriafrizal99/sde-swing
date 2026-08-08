#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.runtime import load_environment_file
from modules.telegram.router import TelegramRouter

WIB = ZoneInfo("Asia/Jakarta")
VALIDATION_STATE = ROOT / "data/state/telegram/report_topic_validation.json"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def definitive_validate_topic(token: str, chat_id: str, thread_id: str, timeout: int = 15) -> tuple[bool, str]:
    """Prove the topic accepts a real message for the exact bot + target chat.

    Telegram has no read-only getForumTopic endpoint.  sendChatAction is not a
    sufficient existence proof, so configuration uses a silent sendMessage and
    immediately deletes that validation message.  The normal portfolio runtime
    does not repeat this probe; it trusts only the persisted validation receipt.
    """
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum lengkap"
    if not str(thread_id).isdigit() or int(thread_id) <= 0:
        return False, f"thread_id tidak valid: {thread_id!r}"

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "message_thread_id": str(thread_id),
                "text": "SDE topic validation — auto delete",
                "disable_notification": "true",
            },
            timeout=max(int(timeout), 1),
        )
    except requests.RequestException as exc:
        return False, f"Telegram API tidak dapat dihubungi: {type(exc).__name__}: {exc}"

    try:
        payload = response.json()
    except Exception:
        return False, f"Telegram API bukan JSON (HTTP {response.status_code})"

    if not response.ok or not payload.get("ok"):
        return False, str(payload.get("description") or f"HTTP {response.status_code}")

    message_id = (payload.get("result") or {}).get("message_id")
    delete_note = ""
    if message_id is not None:
        try:
            delete_resp = requests.post(
                f"https://api.telegram.org/bot{token}/deleteMessage",
                data={"chat_id": chat_id, "message_id": str(message_id)},
                timeout=max(int(timeout), 1),
            )
            delete_payload = delete_resp.json()
            if not delete_resp.ok or not delete_payload.get("ok"):
                delete_note = " | validation message terkirim tetapi gagal auto-delete"
        except Exception:
            delete_note = " | validation message terkirim tetapi gagal auto-delete"

    return True, f"VALID{delete_note}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check dedicated Telegram Report topic routing")
    parser.add_argument("--thread-id", default="", help="Override candidate Report thread ID")
    parser.add_argument(
        "--definitive",
        action="store_true",
        help="Validate with a real silent sendMessage, auto-delete it, and persist validation receipt",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_environment_file(ROOT / ".env")
    scheduler = read_json(ROOT / "config/scheduler.json")
    telegram_cfg = read_json(ROOT / "config/telegram.json")
    router = TelegramRouter(telegram_cfg, os.environ)

    report_thread = str(args.thread_id or effective_thread(router, scheduler, "position_management", "report")).strip()
    market_thread = effective_thread(router, scheduler, "market_outlook", "market_outlook")
    post_thread = effective_thread(router, scheduler, "post_market", "post_market")

    if not report_thread:
        print("[FAILED] Topic Report belum dikonfigurasi.")
        print("Gunakan RUN_SDE.bat > Portfolio Operations > Configure / Cek Topic Telegram Report.")
        return 1
    if not report_thread.isdigit() or int(report_thread) <= 0:
        print(f"[FAILED] Topic Report bukan message_thread_id numerik yang valid: {report_thread!r}")
        return 1

    conflicts = sorted({item for item in (market_thread, post_thread) if item and item == report_thread})
    if conflicts:
        print(f"[FAILED] Topic Report thread_id={report_thread} masih sama dengan Market/Post Market.")
        return 2

    token, chat_id = telegram_credentials(telegram_cfg)
    if not token or not chat_id:
        print("[FAILED] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum lengkap.")
        return 3

    if args.definitive:
        live_ok, live_detail = definitive_validate_topic(token, chat_id, report_thread)
        if not live_ok:
            print(f"[FAILED] Topic Report thread_id={report_thread} ditolak sendMessage Telegram: {live_detail}")
            print("ID ini TIDAK boleh disimpan sebagai TELEGRAM_THREAD_REPORT_ID.")
            return 4
        write_json(
            VALIDATION_STATE,
            {
                "chat_id": str(chat_id),
                "thread_id": str(report_thread),
                "validated_at": datetime.now(WIB).isoformat(timespec="seconds"),
                "method": "sendMessage+deleteMessage",
            },
        )
        print(
            f"[OK] Telegram route DEFINITIVELY VALID: Report={report_thread} | "
            f"Market={market_thread or '-'} | PostMarket={post_thread or '-'}"
        )
        if live_detail != "VALID":
            print(f"[WARNING] {live_detail}")
        return 0

    receipt = read_json(VALIDATION_STATE)
    receipt_thread = str(receipt.get("thread_id") or "").strip()
    receipt_chat = str(receipt.get("chat_id") or "").strip()
    if receipt_thread != report_thread or receipt_chat != str(chat_id):
        print(
            f"[FAILED] Topic Report={report_thread} belum punya validation receipt definitif "
            f"untuk TELEGRAM_CHAT_ID={chat_id}."
        )
        print("Kirim 'REPORT TEST' di topic Report lalu jalankan menu Configure > Auto-detect + Validate.")
        return 5

    print(
        f"[OK] Telegram route VALIDATED: Report={report_thread} | "
        f"Market={market_thread or '-'} | PostMarket={post_thread or '-'} | "
        f"validated_at={receipt.get('validated_at', '-') }"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
