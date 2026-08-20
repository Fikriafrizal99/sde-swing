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

from modules.idx_disclosure.browser_client import (
    PlaywrightAnnouncementClient,
    ResilientAnnouncementSource,
)
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


def _browser_source(cfg: dict, request_cfg: dict) -> PlaywrightAnnouncementClient:
    browser_cfg = request_cfg.get("browser", {})
    return PlaywrightAnnouncementClient(
        endpoint=str(cfg["endpoint"]),
        bootstrap_url=str(
            request_cfg.get(
                "bootstrap_url",
                "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/",
            )
        ),
        timeout_seconds=float(browser_cfg.get("timeout_seconds", 25)),
        emiten_type=str(request_cfg.get("emiten_type", "*")),
        language=str(request_cfg.get("language", "id")),
        keyword=str(request_cfg.get("keyword", "")),
        browser_channels=tuple(browser_cfg.get("channels", ["chrome", "msedge"])),
        headless=bool(browser_cfg.get("headless", False)),
        bootstrap_wait_ms=int(browser_cfg.get("bootstrap_wait_ms", 2500)),
        max_retries=int(browser_cfg.get("max_retries", 1)),
    )


def _direct_source(cfg: dict, request_cfg: dict, transport: str, *, retries: int | None = None):
    return IDXAnnouncementClient(
        endpoint=str(cfg["endpoint"]),
        timeout_seconds=float(request_cfg.get("timeout_seconds", 10)),
        max_retries=int(
            request_cfg.get("max_retries", 3) if retries is None else retries
        ),
        backoff_seconds=tuple(request_cfg.get("backoff_seconds", [2, 4, 8])),
        emiten_type=str(request_cfg.get("emiten_type", "*")),
        language=str(request_cfg.get("language", "id")),
        keyword=str(request_cfg.get("keyword", "")),
        transport=transport,
        browser_impersonate=str(request_cfg.get("browser_impersonate", "chrome")),
        bootstrap_url=str(
            request_cfg.get(
                "bootstrap_url",
                "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/",
            )
        ),
        bootstrap_before_api=bool(request_cfg.get("bootstrap_before_api", True)),
    )


def _make_source(cfg: dict, request_cfg: dict, transport: str):
    if transport == "playwright":
        return _browser_source(cfg, request_cfg)
    if transport == "browser_fallback":
        primary = _direct_source(cfg, request_cfg, "curl_cffi", retries=0)
        return ResilientAnnouncementSource(primary, _browser_source(cfg, request_cfg))
    if transport in {"auto", "curl_cffi", "requests"}:
        return _direct_source(cfg, request_cfg, transport)
    raise IDXClientError(f"Unknown IDX transport: {transport}")


def main() -> int:
    parser = argparse.ArgumentParser(description="IDX Disclosure Watcher V1")
    parser.add_argument("--dry-run", action="store_true", help="Collect and dedup without Telegram")
    parser.add_argument("--date", help="Override Jakarta date as YYYYMMDD")
    parser.add_argument(
        "--transport",
        choices=("browser_fallback", "playwright", "auto", "curl_cffi", "requests"),
        help="Override IDX transport for diagnostics",
    )
    args = parser.parse_args()

    cfg = _load_config()
    if not args.dry_run and not bool(cfg.get("enabled", False)):
        print("IDX Disclosure Watcher is disabled. Use --dry-run for safe validation.")
        return 0

    request_cfg = cfg.get("request", {})
    transport = str(args.transport or request_cfg.get("transport", "browser_fallback"))
    try:
        source = _make_source(cfg, request_cfg, transport)
    except IDXClientError as exc:
        print(json.dumps({"transport": "unavailable", "error": str(exc)}, ensure_ascii=False))
        return 1

    try:
        repo = SQLiteDisclosureRepository(cfg["state"]["sqlite_path"])
        watcher = IDXDisclosureWatcher(
            source,
            repo,
            delivery=None,
            delivery_enabled=False,
            page_size=int(request_cfg.get("page_size", 50)),
        )
        result = watcher.poll_once(jakarta_date=_parse_date(args.date))
        output = {
            "transport": str(getattr(source, "transport_name", transport)),
            **asdict(result),
        }
        primary_error = str(getattr(source, "primary_error", "") or "")
        if primary_error:
            output["primary_transport_error"] = primary_error
        print(json.dumps(output, ensure_ascii=False))
        return 1 if result.error else 0
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
