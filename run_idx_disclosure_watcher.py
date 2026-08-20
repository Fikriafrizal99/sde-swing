"""One-shot runner for the isolated IDX Disclosure Watcher.

Live Telegram delivery and continuous scheduling remain disabled in this phase.
Use --dry-run to validate direct IDX collection and local SQLite dedup safely.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.idx_disclosure.client import IDXAnnouncementClient
from modules.idx_disclosure.repository import SQLiteDisclosureRepository
from modules.idx_disclosure.watcher import IDXDisclosureWatcher


JAKARTA = ZoneInfo("Asia/Jakarta")
CONFIG_PATH = Path("config/idx_disclosure.json")


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _parse_date(value: str | None):
    if not value:
        return datetime.now(JAKARTA).date()
    return datetime.strptime(value, "%Y%m%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description="IDX Disclosure Watcher V1")
    parser.add_argument("--dry-run", action="store_true", help="Collect and dedup without Telegram")
    parser.add_argument("--date", help="Override Jakarta date as YYYYMMDD")
    args = parser.parse_args()

    cfg = _load_config()
    if not args.dry_run and not bool(cfg.get("enabled", False)):
        print("IDX Disclosure Watcher is disabled. Use --dry-run for safe validation.")
        return 0

    request_cfg = cfg.get("request", {})
    source = IDXAnnouncementClient(
        endpoint=str(cfg["endpoint"]),
        timeout_seconds=float(request_cfg.get("timeout_seconds", 10)),
        max_retries=int(request_cfg.get("max_retries", 3)),
        backoff_seconds=tuple(request_cfg.get("backoff_seconds", [2, 4, 8])),
        emiten_type=str(request_cfg.get("emiten_type", "*")),
        language=str(request_cfg.get("language", "id")),
        keyword=str(request_cfg.get("keyword", "")),
    )
    repo = SQLiteDisclosureRepository(cfg["state"]["sqlite_path"])
    watcher = IDXDisclosureWatcher(
        source,
        repo,
        delivery=None,
        delivery_enabled=False,
        page_size=int(request_cfg.get("page_size", 50)),
    )
    result = watcher.poll_once(jakarta_date=_parse_date(args.date))
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
