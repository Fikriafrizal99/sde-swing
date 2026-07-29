#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import sys
import requests

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
    print("Belum ada update. Kirim satu pesan ke bot/channel lalu jalankan lagi.")
    raise SystemExit(0)

seen = set()
for update in results:
    message = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
    chat = message.get("chat", {})
    if not chat and isinstance(message.get("chat"), dict):
        chat = message["chat"]
    chat_id = chat.get("id")
    if chat_id is None or chat_id in seen:
        continue
    seen.add(chat_id)
    print(json.dumps({
        "chat_id": chat_id,
        "type": chat.get("type"),
        "title": chat.get("title"),
        "username": chat.get("username"),
        "first_name": chat.get("first_name"),
    }, ensure_ascii=False, indent=2))
