#!/usr/bin/env python3
from __future__ import annotations

"""Interactive local settings for Telegram routing and Brave News Search.

Secrets stay in ignored local files and Windows User environment variables.
Existing project .env values remain supported as a compatibility fallback.
"""

import argparse
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOCAL_TELEGRAM = PROJECT_ROOT / "config/telegram.json"
LOCAL_NEWS = PROJECT_ROOT / "config/news.local.json"
DOTENV = PROJECT_ROOT / ".env"
SCHEDULER = PROJECT_ROOT / "config/scheduler.json"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/news/search"

TOPIC_KEYS: dict[str, tuple[str, ...]] = {
    "market": (
        "market_outlook",
        "post_market",
        "broker_summary",
        "broker_summary_csv",
        "broker_multiday",
        "broker_multiday_csv",
    ),
    "final_watchlist": (
        "final_watchlist",
        "final_watchlist_summary",
        "final_watchlist_detail",
        "final_watchlist_csv",
    ),
    "signal_detail": ("signal_detail",),
    "report": ("report", "evaluation", "position_management"),
    "system": ("system", "data_warning", "startup", "source_health", "config_error", "dependency_failure"),
    "news": ("news", "morning_news", "post_market_news"),
}

ENV_TOPIC = {
    "report": "TELEGRAM_THREAD_REPORT_ID",
    "system": "TELEGRAM_THREAD_SYSTEM_ID",
    "news": "TELEGRAM_THREAD_NEWS_ID",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def dotenv_values(path: Path = DOTENV) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def runtime_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    return dotenv_values().get(name, "").strip()


def persist_env(name: str, value: str) -> None:
    os.environ[name] = value
    if os.name == "nt":
        proc = subprocess.run(["setx", name, value], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"setx gagal untuk {name}: {proc.stderr.strip() or proc.stdout.strip()}")


def _telegram_section(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.setdefault("telegram", {})
    if not isinstance(section, dict):
        section = {}
        cfg["telegram"] = section
    return section


def _routing_section(cfg: dict[str, Any]) -> dict[str, Any]:
    ui = cfg.setdefault("telegram_ui", {})
    if not isinstance(ui, dict):
        ui = {}
        cfg["telegram_ui"] = ui
    routing = ui.setdefault("topic_routing", {})
    if not isinstance(routing, dict):
        routing = {}
        ui["topic_routing"] = routing
    return routing


def telegram_credentials() -> tuple[str, str]:
    cfg = load_json(LOCAL_TELEGRAM)
    tg = cfg.get("telegram", {}) if isinstance(cfg.get("telegram", {}), dict) else {}
    dotenv = dotenv_values()
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        or str(tg.get("bot_token", "") or "").strip()
        or dotenv.get("TELEGRAM_BOT_TOKEN", "").strip()
    )
    chat = (
        os.getenv("TELEGRAM_CHAT_ID", "").strip()
        or str(tg.get("chat_id", "") or "").strip()
        or dotenv.get("TELEGRAM_CHAT_ID", "").strip()
    )
    return token, chat


def mask_secret(value: str) -> str:
    if not value:
        return "NOT SET"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:3] + ("*" * max(4, len(value) - 7)) + value[-4:]


def telegram_request(method: str, *, data: dict[str, Any] | None = None, token: str = "") -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang.")
    actual_token = token or telegram_credentials()[0]
    if not actual_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum dikonfigurasi.")
    response = requests.post(
        f"https://api.telegram.org/bot{actual_token}/{method}",
        data=data or {},
        timeout=20,
    )
    try:
        body = response.json()
    except Exception as exc:
        raise RuntimeError(f"Telegram response bukan JSON (HTTP {response.status_code})") from exc
    if not response.ok or not body.get("ok"):
        raise RuntimeError(str(body))
    return body


def validate_token(token: str) -> dict[str, Any]:
    return telegram_request("getMe", token=token).get("result", {})


def validate_chat(chat_id: str, token: str = "") -> dict[str, Any]:
    return telegram_request("getChat", data={"chat_id": chat_id}, token=token).get("result", {})


def validate_topic(thread_id: str, label: str = "SDE ROUTE TEST") -> None:
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        raise RuntimeError("Set Bot Token dan Chat ID terlebih dahulu.")
    body = telegram_request(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "message_thread_id": thread_id,
            "text": f"{label} - validation",
            "disable_notification": "true",
        },
    )
    message_id = body.get("result", {}).get("message_id")
    if message_id:
        try:
            telegram_request("deleteMessage", data={"chat_id": chat_id, "message_id": message_id})
        except Exception:
            pass


def set_token() -> int:
    token = getpass.getpass("Telegram Bot Token (input disembunyikan): ").strip()
    if not token:
        print("[FAILED] Token kosong.")
        return 1
    try:
        bot = validate_token(token)
    except Exception as exc:
        print(f"[FAILED] Token tidak valid: {exc}")
        return 1
    cfg = load_json(LOCAL_TELEGRAM)
    _telegram_section(cfg)["bot_token"] = token
    save_json(LOCAL_TELEGRAM, cfg)
    persist_env("TELEGRAM_BOT_TOKEN", token)
    print(f"[OK] Bot token valid dan tersimpan lokal. Bot=@{bot.get('username', '-')}")
    return 0


def set_chat() -> int:
    chat_id = input("Telegram Chat ID: ").strip()
    if not chat_id:
        print("[FAILED] Chat ID kosong.")
        return 1
    try:
        chat = validate_chat(chat_id)
    except Exception as exc:
        print(f"[FAILED] Chat ID tidak valid/tidak dapat diakses bot: {exc}")
        return 1
    cfg = load_json(LOCAL_TELEGRAM)
    _telegram_section(cfg)["chat_id"] = chat_id
    save_json(LOCAL_TELEGRAM, cfg)
    persist_env("TELEGRAM_CHAT_ID", chat_id)
    print(f"[OK] Chat ID tersimpan. Title={chat.get('title') or chat.get('username') or chat.get('id')}")
    return 0


def set_topic(name: str, thread_id: str = "") -> int:
    if name not in TOPIC_KEYS:
        print(f"[FAILED] Topic group tidak dikenal: {name}")
        return 1
    thread = (thread_id or input(f"message_thread_id untuk {name}: ")).strip()
    if not thread.isdigit() or int(thread) <= 0:
        print("[FAILED] message_thread_id harus angka positif.")
        return 1
    try:
        validate_topic(thread, f"SDE {name.upper()} TOPIC")
    except Exception as exc:
        print(f"[FAILED] Topic {thread} gagal divalidasi: {exc}")
        return 1

    cfg = load_json(LOCAL_TELEGRAM)
    routing = _routing_section(cfg)
    for key in TOPIC_KEYS[name]:
        routing[key] = thread
    save_json(LOCAL_TELEGRAM, cfg)

    env_name = ENV_TOPIC.get(name)
    if env_name:
        persist_env(env_name, thread)
    print(f"[OK] Topic {name}={thread} tervalidasi dan tersimpan lokal.")
    return 0


def _scheduler_route(key: str) -> str:
    scheduler = load_json(SCHEDULER)
    return str(scheduler.get("delivery", {}).get("topic_routing", {}).get(key, "") or "").strip()


def effective_topic(name: str) -> str:
    cfg = load_json(LOCAL_TELEGRAM)
    ui = cfg.get("telegram_ui", {}) if isinstance(cfg.get("telegram_ui", {}), dict) else {}
    routing = ui.get("topic_routing", {}) if isinstance(ui.get("topic_routing", {}), dict) else {}
    env_name = ENV_TOPIC.get(name)
    if env_name:
        env_value = runtime_value(env_name)
        if env_value.isdigit():
            return env_value
    for key in TOPIC_KEYS[name]:
        value = str(routing.get(key, "") or "").strip()
        if value.isdigit():
            return value
    for key in TOPIC_KEYS[name]:
        value = _scheduler_route(key)
        if value.isdigit():
            return value
    return ""


def brave_key() -> str:
    cfg = load_json(LOCAL_NEWS)
    dotenv = dotenv_values()
    return (
        os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        or str(cfg.get("brave_search_api_key", "") or "").strip()
        or dotenv.get("BRAVE_SEARCH_API_KEY", "").strip()
    )


def _brave_error(response: Any) -> str:
    try:
        payload = response.json()
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(getattr(response, "text", "") or "").strip()[:1000]


def validate_brave(key: str) -> None:
    """Validate the key with the smallest documented News Search request.

    Do not attach country/search_lang here. Some Brave plans/enum revisions can
    reject unsupported locale combinations with HTTP 422 even when the key is
    valid. Production localization is handled by the search query itself.
    """
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang.")
    response = requests.get(
        BRAVE_ENDPOINT,
        params={"q": "IHSG", "freshness": "pd", "count": 1},
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": key,
        },
        timeout=15,
    )
    if response.status_code == 429:
        raise RuntimeError("BRAVE_RATE_LIMIT")
    if response.status_code == 422:
        raise RuntimeError(f"BRAVE_REQUEST_INVALID: {_brave_error(response)}")
    if not response.ok:
        raise RuntimeError(f"BRAVE_HTTP_{response.status_code}: {_brave_error(response)}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("Brave response bukan JSON.") from exc
    if payload.get("type") not in {"news", None}:
        raise RuntimeError(f"Unexpected Brave response type: {payload.get('type')}")


def status() -> int:
    token, chat = telegram_credentials()
    brave = brave_key()
    print("============================================================")
    print("                 SDE - TELEGRAM SETTINGS")
    print("============================================================")
    print(f"TELEGRAM_BOT_TOKEN : {mask_secret(token)}")
    print(f"TELEGRAM_CHAT_ID   : {chat or 'NOT SET'}")
    print(f"BRAVE_SEARCH_KEY   : {mask_secret(brave)}")
    print("")
    for name in ("market", "final_watchlist", "signal_detail", "report", "system", "news"):
        print(f"{name.upper():16} : {effective_topic(name) or 'NOT SET'}")
    generic_signal = runtime_value("TELEGRAM_THREAD_SIGNAL_ID")
    if generic_signal:
        print("")
        print(
            f"[INFO] TELEGRAM_THREAD_SIGNAL_ID={generic_signal} dipertahankan sebagai compatibility fallback; "
            "route SIGNAL spesifik tetap memiliki prioritas."
        )
    return 0


def validate_credentials() -> int:
    token, chat = telegram_credentials()
    if not token or not chat:
        print("[FAILED] Token/Chat ID belum lengkap.")
        return 1
    try:
        bot = validate_token(token)
        room = validate_chat(chat, token)
    except Exception as exc:
        print(f"[FAILED] Telegram credentials: {exc}")
        return 1
    print(f"[OK] Bot @{bot.get('username', '-')} dapat mengakses chat {room.get('title') or room.get('id')}.")
    return 0


def test_all_topics() -> int:
    failures = 0
    for name in ("market", "final_watchlist", "signal_detail", "report", "system", "news"):
        thread = effective_topic(name)
        if not thread:
            print(f"[SKIPPED] {name}: belum dikonfigurasi.")
            failures += 1
            continue
        try:
            validate_topic(thread, f"SDE {name.upper()} ROUTE")
            print(f"[OK] {name}: {thread}")
        except Exception as exc:
            print(f"[FAILED] {name}: {thread} -> {exc}")
            failures += 1
    return 0 if failures == 0 else 2


def set_brave() -> int:
    key = getpass.getpass("Brave Search API Key (input disembunyikan): ").strip()
    if not key:
        print("[FAILED] Brave key kosong.")
        return 1
    try:
        validate_brave(key)
    except Exception as exc:
        print(f"[FAILED] Brave key tidak valid/tidak dapat digunakan: {exc}")
        return 1
    save_json(LOCAL_NEWS, {"brave_search_api_key": key})
    persist_env("BRAVE_SEARCH_API_KEY", key)
    print("[OK] Brave Search API Key tervalidasi dan tersimpan lokal.")
    return 0


def test_brave() -> int:
    key = brave_key()
    if not key:
        print("[FAILED] BRAVE_SEARCH_API_KEY belum dikonfigurasi.")
        return 1
    try:
        validate_brave(key)
    except Exception as exc:
        print(f"[FAILED] Brave Search API: {exc}")
        return 1
    print("[OK] Brave Search API terhubung.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure local Telegram and Brave settings")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("set-token")
    sub.add_parser("set-chat")
    sub.add_parser("validate-credentials")
    topic = sub.add_parser("set-topic")
    topic.add_argument("--name", choices=sorted(TOPIC_KEYS), required=True)
    topic.add_argument("--thread-id", default="")
    sub.add_parser("status")
    sub.add_parser("test-all")
    sub.add_parser("set-brave")
    sub.add_parser("test-brave")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "set-token":
        return set_token()
    if args.command == "set-chat":
        return set_chat()
    if args.command == "validate-credentials":
        return validate_credentials()
    if args.command == "set-topic":
        return set_topic(args.name, args.thread_id)
    if args.command == "status":
        return status()
    if args.command == "test-all":
        return test_all_topics()
    if args.command == "set-brave":
        return set_brave()
    if args.command == "test-brave":
        return test_brave()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
