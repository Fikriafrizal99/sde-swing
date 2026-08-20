"""One-shot runner for the isolated IDX Disclosure Watcher.

Live Telegram delivery and continuous scheduling remain disabled in this phase.
Use --dry-run to validate IDX collection and local SQLite dedup safely.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.idx_disclosure.client import IDXAnnouncementClient, IDXClientError
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
    parser.add_argument(
        "--transport",
        choices=("auto", "curl_cffi", "requests"),
        help="Override IDX HTTP transport for diagnostics",
    )
    args = parser.parse_args()

    cfg = _load_config()
    if not args.dry_run and not bool(cfg.get("enabled", False)):
        print("IDX Disclosure Watcher is disabled. Use --dry-run for safe validation.")
        return 0

    request_cfg = cfg.get("request", {})
    try:
        source = IDXAnnouncementClient(
            endpoint=str(cfg["endpoint"]),
            timeout_seconds=float(request_cfg.get("timeout_seconds", 10)),
            max_retries=int(request_cfg.get("max_retries", 3)),
            backoff_seconds=tuple(request_cfg.get("backoff_seconds", [2, 4, 8])),
            emiten_type=str(request_cfg.get("emiten_type", "*")),
            language=str(request_cfg.get("language", "id")),
            keyword=str(request_cfg.get("keyword", "")),
            transport=str(args.transport or request_cfg.get("transport", "auto")),
            browser_impersonate=str(request_cfg.get("browser_impersonate", "chrome")),
            bootstrap_url=str(
                request_cfg.get(
                    "bootstrap_url",
                    "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/",
                )
            ),
            bootstrap_before_api=bool(request_cfg.get("bootstrap_before_api", True)),
        )
    except IDXClientError as exc:
        print(json.dumps({"transport": "unavailable", "error": str(exc)}, ensure_ascii=False))
        return 1

    repo = SQLiteDisclosureRepository(cfg["state"]["sqlite_path"])
    watcher = IDXDisclosureWatcher(
        source,
        repo,
        delivery=None,
        delivery_enabled=False,
        page_size=int(request_cfg.get("page_size", 50)),
    )
    result = watcher.poll_once(jakarta_date=_parse_date(args.date))
    output = {"transport": source.transport_name, **asdict(result)}
    print(json.dumps(output, ensure_ascii=False))
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
