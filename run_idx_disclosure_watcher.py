"""Runner for the isolated IDX Disclosure Watcher.

Supports one-shot validation and a persistent polling loop. The Playwright
source stays alive across polls so Chrome/Edge is not reopened every minute.
Optional Groq document reading is downstream and cannot block official IDX
notification delivery.
"""

from __future__ import annotations

import argparse
import json
import signal
import threading
from dataclasses import asdict
from datetime import datetime, time as dt_time
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from modules.idx_disclosure.ai_reader import (
    AIReaderPermanentError,
    GroqDisclosureAIReader,
    load_environment_file,
)
from modules.idx_disclosure.ai_state import SQLiteDisclosureAIQueue
from modules.idx_disclosure.browser_client import (
    PlaywrightAnnouncementClient,
    ResilientAnnouncementSource,
)
from modules.idx_disclosure.client import IDXAnnouncementClient, IDXClientError
from modules.idx_disclosure.repository import SQLiteDisclosureRepository
from modules.idx_disclosure.telegram_delivery import (
    TelegramDeliveryError,
    TelegramNewsDelivery,
)
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
        primary_transport = str(request_cfg.get("fallback_primary_transport", "curl_cffi"))
        primary = _direct_source(cfg, request_cfg, primary_transport, retries=0)
        return ResilientAnnouncementSource(primary, _browser_source(cfg, request_cfg))
    return _direct_source(cfg, request_cfg, transport)


def poll_interval_seconds(now: datetime, cfg: dict) -> int:
    """Choose the configured interval for the current Jakarta wall clock."""
    polling = cfg.get("polling", {})
    market = polling.get("market_window", {})
    evening = polling.get("evening_window", {})
    current = now.timetz().replace(tzinfo=None)

    def parse_clock(value: str, fallback: str) -> dt_time:
        raw = str(value or fallback)
        return datetime.strptime(raw, "%H:%M").time()

    market_start = parse_clock(market.get("start"), "08:00")
    market_end = parse_clock(market.get("end"), "17:00")
    evening_start = parse_clock(evening.get("start"), "17:00")
    evening_end = parse_clock(evening.get("end"), "22:00")

    if market_start <= current < market_end:
        return max(1, int(market.get("interval_seconds", 60)))
    if evening_start <= current < evening_end:
        return max(1, int(evening.get("interval_seconds", 180)))
    return max(1, int(polling.get("overnight_interval_seconds", 600)))


def _build_delivery(cfg: dict) -> TelegramNewsDelivery:
    delivery_cfg = cfg.get("delivery", {})
    return TelegramNewsDelivery(
        scheduler_config_path=str(
            delivery_cfg.get("scheduler_config_path", "config/scheduler.json")
        ),
        telegram_config_path=str(
            delivery_cfg.get("telegram_config_path", "config/telegram.json")
        ),
        timeout_seconds=float(delivery_cfg.get("request_timeout_seconds", 30)),
    )


def _build_ai(
    cfg: dict,
    *,
    delivery_enabled: bool,
    dry_run: bool,
    no_ai: bool,
):
    ai_cfg = cfg.get("ai_reader", {})
    if no_ai:
        return None, None, "disabled:cli"
    if dry_run:
        return None, None, "disabled:dry-run"
    if not delivery_enabled:
        return None, None, "disabled:no-telegram"
    if not bool(ai_cfg.get("enabled", False)):
        return None, None, "disabled:config"
    if str(ai_cfg.get("provider", "groq")).lower() != "groq":
        return None, None, "disabled:unsupported-provider"

    load_environment_file(str(ai_cfg.get("env_file", ".env")))
    dependency_ok, dependency_error = GroqDisclosureAIReader.dependency_available()
    if not dependency_ok:
        return None, None, f"disabled:{dependency_error.lower()}"

    try:
        reader = GroqDisclosureAIReader.from_config(ai_cfg)
    except AIReaderPermanentError as exc:
        return None, None, f"disabled:{str(exc).lower()}"

    queue = SQLiteDisclosureAIQueue(cfg["state"]["sqlite_path"])
    return reader, queue, f"groq:{reader.model}"


def _output(source, result, *, mode: str, ai_status: str) -> dict:
    return {
        "time": datetime.now(JAKARTA).isoformat(timespec="seconds"),
        "mode": mode,
        "transport": str(getattr(source, "transport_name", type(source).__name__)),
        "ai_reader": ai_status,
        **asdict(result),
    }


def _close_source(source) -> None:
    close = getattr(source, "close", None)
    if callable(close):
        close()


def main() -> int:
    parser = argparse.ArgumentParser(description="IDX Disclosure Watcher V1 + optional Groq reader")
    parser.add_argument(
        "--dry-run", action="store_true", help="Collect and dedup without Telegram/AI"
    )
    parser.add_argument(
        "--watch", action="store_true", help="Keep polling using configured intervals"
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Enable Telegram delivery to the existing NEWS topic",
    )
    parser.add_argument("--date", help="Override Jakarta date as YYYYMMDD (one-shot only)")
    parser.add_argument(
        "--transport",
        choices=("playwright", "browser_fallback", "auto", "curl_cffi", "requests"),
        help="Override IDX transport",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable the optional Groq document reader without changing config",
    )
    parser.add_argument(
        "--ai-backfill-latest",
        type=int,
        default=0,
        metavar="N",
        help="Explicitly queue up to N latest already-delivered IDX disclosures for AI summary",
    )
    args = parser.parse_args()

    if args.watch and args.date:
        parser.error("--date cannot be used with --watch")
    if args.dry_run and args.telegram:
        parser.error("--dry-run and --telegram cannot be used together")
    if args.dry_run and args.ai_backfill_latest:
        parser.error("--ai-backfill-latest cannot be used with --dry-run")
    if args.no_ai and args.ai_backfill_latest:
        parser.error("--ai-backfill-latest cannot be used with --no-ai")
    if args.ai_backfill_latest < 0:
        parser.error("--ai-backfill-latest must be >= 0")

    cfg = _load_config()
    request_cfg = cfg.get("request", {})
    transport = str(args.transport or request_cfg.get("transport", "playwright"))

    explicit_live = bool(args.watch or args.telegram)
    if not args.dry_run and not explicit_live and not bool(cfg.get("enabled", False)):
        print("IDX Disclosure Watcher is disabled. Use --dry-run or explicit --watch/--telegram.")
        return 0

    delivery_enabled = bool(
        not args.dry_run
        and (
            args.telegram
            or bool(cfg.get("delivery", {}).get("enabled", False))
        )
    )
    if args.watch and not args.dry_run and not delivery_enabled:
        parser.error(
            "Live --watch requires --telegram (or delivery.enabled=true). "
            "Use --dry-run --watch for log-only monitoring."
        )

    try:
        source = _make_source(cfg, request_cfg, transport)
    except IDXClientError as exc:
        print(json.dumps({"transport": "unavailable", "error": str(exc)}, ensure_ascii=False))
        return 1

    try:
        delivery = _build_delivery(cfg) if delivery_enabled else None
    except TelegramDeliveryError as exc:
        _close_source(source)
        print(
            json.dumps(
                {"transport": getattr(source, "transport_name", transport), "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1

    # Initialize the official disclosure store first; the optional AI queue has
    # a foreign key to this isolated IDX state database.
    repo = SQLiteDisclosureRepository(cfg["state"]["sqlite_path"])

    ai_processor, ai_queue, ai_status = _build_ai(
        cfg,
        delivery_enabled=delivery_enabled,
        dry_run=args.dry_run,
        no_ai=args.no_ai,
    )
    print(f"[IDX AI] {ai_status}", flush=True)

    if args.ai_backfill_latest and ai_queue is None:
        print("[IDX AI] backfill skipped because AI reader is not available", flush=True)

    if args.ai_backfill_latest and ai_queue is not None:
        queued = ai_queue.enqueue_latest_delivered(
            limit=args.ai_backfill_latest,
            queued_at=datetime.now(JAKARTA),
        )
        print(f"[IDX AI] backfill queued: {queued}", flush=True)

    ai_cfg = cfg.get("ai_reader", {})
    retry_cfg = ai_cfg.get("retry", {}) if isinstance(ai_cfg.get("retry", {}), dict) else {}
    watcher = IDXDisclosureWatcher(
        source,
        repo,
        delivery=delivery,
        delivery_enabled=delivery_enabled,
        page_size=int(request_cfg.get("page_size", 50)),
        ai_processor=ai_processor,
        ai_queue=ai_queue,
        ai_enabled=ai_processor is not None and ai_queue is not None,
        ai_max_attempts=int(retry_cfg.get("max_attempts", 3)),
        ai_retry_backoff_seconds=tuple(retry_cfg.get("backoff_seconds", [60, 300, 900])),
        ai_max_documents_per_poll=int(ai_cfg.get("max_documents_per_poll", 1)),
    )

    if not args.watch:
        result = watcher.poll_once(jakarta_date=_parse_date(args.date))
        print(
            json.dumps(
                _output(
                    source,
                    result,
                    mode="dry-run" if args.dry_run else "once",
                    ai_status=ai_status,
                ),
                ensure_ascii=False,
            )
        )
        _close_source(source)
        return 1 if result.error else 0

    stop_event = threading.Event()

    def request_stop(signum, frame):  # noqa: ARG001
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, request_stop)
        except (ValueError, OSError):
            pass

    mode = "watch-dry-run" if args.dry_run else "watch-live"
    try:
        while not stop_event.is_set():
            started = monotonic()
            now = datetime.now(JAKARTA)
            result = watcher.poll_once(jakarta_date=now.date())
            print(
                json.dumps(_output(source, result, mode=mode, ai_status=ai_status), ensure_ascii=False),
                flush=True,
            )

            interval = poll_interval_seconds(now, cfg)
            elapsed = monotonic() - started
            wait_seconds = max(1.0, interval - elapsed)
            stop_event.wait(wait_seconds)
    finally:
        _close_source(source)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
