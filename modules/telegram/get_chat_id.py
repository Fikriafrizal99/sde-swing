#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.runtime import load_environment_file


def _topic_name(message: dict[str, Any]) -> str:
    created = message.get("forum_topic_created") or {}
    if isinstance(created, dict) and created.get("name"):
        return str(created.get("name"))
    edited = message.get("forum_topic_edited") or {}
    if isinstance(edited, dict) and edited.get("name"):
        return str(edited.get("name"))
    reply = message.get("reply_to_message") or {}
    created = reply.get("forum_topic_created") or {}
    if isinstance(created, dict) and created.get("name"):
        return str(created.get("name"))
    return ""


load_environment_file(ROOT / ".env")
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
target_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
if not token:
    print("Set TELEGRAM_BOT_TOKEN terlebih dahulu.", file=sys.stderr)
    raise SystemExit(1)

resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
payload = resp.json()
if not payload.get("ok"):
    print(json.dumps(payload, indent=2))
    raise SystemExit(1)

results = payload.get("result", [])
if not results:
    print("Belum ada update. Kirim 'REPORT TEST' di topic Report lalu jalankan lagi.")
    raise SystemExit(0)

chats: dict[str, dict[str, Any]] = {}
topics: dict[tuple[str, str], dict[str, Any]] = {}
for update in results:
    message = update.get("message") or update.get("channel_post") or update.get("edited_message") or {}
    if not isinstance(message, dict):
        continue
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        continue
    chat_key = str(chat_id)
    chats[chat_key] = {
        "chat_id": chat_id,
        "type": chat.get("type"),
        "title": chat.get("title"),
        "username": chat.get("username"),
        "first_name": chat.get("first_name"),
        "is_sde_target_chat": bool(target_chat_id and chat_key == target_chat_id),
    }
    thread_id = message.get("message_thread_id")
    if thread_id is None:
        continue
    topic_key = (chat_key, str(thread_id))
    topics[topic_key] = {
        "chat_id": chat_id,
        "message_thread_id": thread_id,
        "topic_name": _topic_name(message),
        "latest_text": str(message.get("text") or message.get("caption") or "")[:120],
        "is_sde_target_chat": bool(target_chat_id and chat_key == target_chat_id),
    }

print("=== TELEGRAM TARGET CHAT ===")
if target_chat_id:
    print(f"TELEGRAM_CHAT_ID={target_chat_id}")
    target_chat = chats.get(target_chat_id)
    if target_chat:
        print(json.dumps(target_chat, ensure_ascii=False, indent=2))
    else:
        print("Belum ada update terbaru yang terbaca dari TELEGRAM_CHAT_ID tersebut.")
else:
    print("TELEGRAM_CHAT_ID belum dikonfigurasi; jangan memilih thread ID sebelum chat target jelas.")

print("\n=== FORUM TOPICS DARI CHAT SDE ===")
target_topics = [item for (chat_key, _), item in topics.items() if not target_chat_id or chat_key == target_chat_id]
if not target_topics:
    print("Belum ada topic update dari chat SDE. Kirim 'REPORT TEST' di topic Report lalu jalankan lagi.")
else:
    for item in target_topics:
        print(json.dumps(item, ensure_ascii=False, indent=2))

other_count = sum(1 for (chat_key, _) in topics if target_chat_id and chat_key != target_chat_id)
if other_count:
    print(f"\n[INFO] {other_count} topic update dari chat lain disembunyikan agar tidak salah pilih thread ID.")

print("\nGunakan message_thread_id dari topic Report pada CHAT SDE di atas sebagai:")
print("  TELEGRAM_THREAD_REPORT_ID=<message_thread_id>")
print("Setelah itu WAJIB jalankan live validation dari menu konfigurasi.")
