#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests


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


token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
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
    print("Belum ada update. Kirim satu pesan ke bot/group lalu jalankan lagi.")
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
    }

print("=== TELEGRAM CHAT ===")
for item in chats.values():
    print(json.dumps(item, ensure_ascii=False, indent=2))

print("\n=== FORUM TOPICS / THREAD IDS ===")
if not topics:
    print("Belum ada topic update yang terbaca. Kirim pesan seperti 'REPORT TEST' di topic Report lalu jalankan lagi.")
else:
    for item in topics.values():
        print(json.dumps(item, ensure_ascii=False, indent=2))

print("\nUntuk SDE topic Report, gunakan thread ID dari topic Report sebagai:")
print("  TELEGRAM_THREAD_REPORT_ID=<message_thread_id>")
print("Windows persistent example:")
print("  setx TELEGRAM_THREAD_REPORT_ID <message_thread_id>")
