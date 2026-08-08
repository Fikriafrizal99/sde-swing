#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.runtime import load_environment_file


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def credentials() -> tuple[str, str]:
    load_environment_file(ROOT / ".env")
    telegram_cfg = read_json(ROOT / "config/telegram.json")
    runtime_cfg = telegram_cfg.get("telegram", {}) if isinstance(telegram_cfg.get("telegram", {}), dict) else {}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or str(runtime_cfg.get("bot_token", "") or "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or str(runtime_cfg.get("chat_id", "") or "").strip()
    return token, chat_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect Report topic ID from a marker message in the configured SDE chat")
    parser.add_argument("--marker", default="REPORT TEST")
    parser.add_argument("--id-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    marker = str(args.marker or "REPORT TEST").strip().casefold()
    token, chat_id = credentials()
    if not token or not chat_id:
        print("[FAILED] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum lengkap.", file=sys.stderr)
        return 1

    try:
        response = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
        payload = response.json()
    except Exception as exc:
        print(f"[FAILED] getUpdates gagal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if not response.ok or not payload.get("ok"):
        print(f"[FAILED] getUpdates ditolak Telegram: {payload}", file=sys.stderr)
        return 3

    matches: list[tuple[int, str, str]] = []
    for update in payload.get("result", []):
        message = update.get("message") or update.get("channel_post") or update.get("edited_message") or {}
        if not isinstance(message, dict):
            continue
        current_chat_id = str((message.get("chat") or {}).get("id") or "").strip()
        if current_chat_id != str(chat_id):
            continue
        text = str(message.get("text") or message.get("caption") or "").strip()
        if text.casefold() != marker:
            continue
        thread_id = message.get("message_thread_id")
        if thread_id is None:
            continue
        matches.append((int(update.get("update_id") or 0), str(thread_id), text))

    if not matches:
        print(
            f"[FAILED] Pesan marker {args.marker!r} belum ditemukan di TELEGRAM_CHAT_ID={chat_id}.\n"
            f"Kirim tepat '{args.marker}' di topic Report, lalu ulangi.",
            file=sys.stderr,
        )
        return 4

    matches.sort(key=lambda item: item[0], reverse=True)
    _, thread_id, text = matches[0]
    if args.id_only:
        print(thread_id)
    else:
        print(f"[OK] Marker ditemukan di chat SDE: text={text!r} | message_thread_id={thread_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
