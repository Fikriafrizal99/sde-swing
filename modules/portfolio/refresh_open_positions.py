#!/usr/bin/env python3
"""Refresh OHLCV only for actual OPEN portfolio positions.

This is an isolated portfolio preflight.  It reuses the existing historical
Downloader and writes to the same canonical historical directory, but it does
not change the Discovery universe, candidate ranking, Decision Engine, Exit
Engine, or Final Watchlist.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.broker_bridge.broker_navigator_export import read_open_portfolio_symbols
from swing_utils import make_run_id

WIB = ZoneInfo("Asia/Jakarta")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve(value: str | Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


def evaluation_datetime(trade_date: str, market_close: str) -> str:
    hour, minute = (int(part) for part in market_close.split(":", 1))
    day = datetime.fromisoformat(trade_date).date()
    # One minute after configured close ensures LAST_CLOSED_CANDLE semantics.
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=WIB).replace(second=0) .__add__(timedelta(minutes=1)).isoformat(timespec="seconds")


def write_symbols(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Symbol"])
        writer.writerows([[symbol] for symbol in symbols])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh historical data for actual OPEN portfolio positions")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--trade-date", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_json(resolve(args.config))
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    freshness = config.get("data_freshness", {}) if isinstance(config.get("data_freshness"), dict) else {}
    download = config.get("download", {}) if isinstance(config.get("download"), dict) else {}

    db_path = resolve(paths.get("swing_database", "data/database/sde_swing_history.db"))
    symbols = read_open_portfolio_symbols(db_path)
    if not symbols:
        print("PORTFOLIO HISTORY REFRESH: SKIPPED_NO_OPEN_POSITION")
        return 0

    symbol_file = resolve("data/state/portfolio/OPEN_PORTFOLIO_SYMBOLS.csv")
    write_symbols(symbol_file, symbols)

    historical_dir = resolve(paths.get("historical_dir", "data/output/historical/by_symbol"))
    historical_root = historical_dir.parent
    manifest_dir = resolve(paths.get("manifest_dir", "data/output/manifests"))
    downloader = resolve(paths.get("historical_downloader", "modules/historical_downloader/historical_downloader.py"))
    market_close = str(freshness.get("market_close", "16:15"))
    run_id = make_run_id(prefix="SDE-POSITION-REFRESH")

    command = [
        sys.executable,
        "-u",
        str(downloader),
        str(symbol_file),
        "--output",
        str(historical_root),
        "--period",
        str(download.get("period", "2y")),
        "--pause",
        str(download.get("pause", 0.35)),
        "--run-id",
        run_id,
        "--manifest-dir",
        str(manifest_dir),
        "--daily-candle-policy",
        str(freshness.get("daily_candle_policy", "LAST_CLOSED_CANDLE")),
        "--yahoo-failure-policy",
        "USE_LAST_VALID",
        "--market-close",
        market_close,
        "--after-midnight-cutoff",
        str(freshness.get("after_midnight_cutoff", "06:00")),
        "--repair-overlap-sessions",
        str(freshness.get("yahoo_repair_overlap_sessions", 5)),
        "--batch-size",
        str(freshness.get("yahoo_batch_size", 50)),
        "--max-workers",
        str(freshness.get("yahoo_max_workers", 4)),
        "--request-delay-seconds",
        str(freshness.get("yahoo_request_delay_seconds", 1)),
        "--max-retries",
        str(freshness.get("yahoo_max_retries", download.get("retries", 3))),
        "--evaluation-datetime",
        evaluation_datetime(args.trade_date, market_close),
    ]
    if freshness.get("yahoo_batch_enabled", True):
        command.append("--batch-enabled")
    for day in freshness.get("market_holidays", []) or []:
        command.extend(["--market-holiday", str(day)])
    for day in freshness.get("special_trading_days", []) or []:
        command.extend(["--special-trading-day", str(day)])

    print(
        f"PORTFOLIO HISTORY REFRESH: {len(symbols)} OPEN symbol(s) | "
        f"{','.join(symbols)}",
        flush=True,
    )
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    if completed.returncode != 0:
        print(
            f"[WARNING] Portfolio OHLCV refresh exit code {completed.returncode}; "
            "Position Management may continue with last valid local data.",
            flush=True,
        )
        return completed.returncode
    print("PORTFOLIO HISTORY REFRESH: SUCCESS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
