#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
        print("Gunakan RUN_SDE.bat > System & Status > Configure / Cek Telegram Topic IDs.")
        return 1

    conflicts = sorted({item for item in (market_thread, post_thread) if item and item == report_thread})
    if conflicts:
        print(
            f"[FAILED] Topic Report thread_id={report_thread} masih sama dengan Market/Post Market."
        )
        print("Set TELEGRAM_THREAD_REPORT_ID ke thread ID topic Report yang berbeda.")
        return 2

    print(
        f"[OK] Telegram route: Report={report_thread} | "
        f"Market={market_thread or '-'} | PostMarket={post_thread or '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
