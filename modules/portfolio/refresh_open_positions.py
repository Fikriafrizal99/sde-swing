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
YAHOO_IMPORT_TIMEOUT_SECONDS = 20
YAHOO_RUNTIME_FAILURE_EXIT = 71
REQUIRED_CANDLE_FIELDS = ("date", "open", "high", "low", "close", "volume")


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
    closed_at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=WIB)
    return (closed_at + timedelta(minutes=1)).isoformat(timespec="seconds")


def write_symbols(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Symbol"])
        writer.writerows([[symbol] for symbol in symbols])


def _normalized_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    return text[:-3] if text.endswith(".JK") else text


def _history_file(historical_dir: Path, symbol: str) -> Path | None:
    for candidate in (
        historical_dir / f"{symbol}.csv",
        historical_dir / f"{symbol}.JK.csv",
    ):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _valid_closed_candle(
    path: Path,
    *,
    symbol: str,
    trade_date: str,
    market_close: str,
) -> tuple[bool, str]:
    try:
        close_hour, close_minute = (int(part) for part in market_close.split(":", 1))
        target_day = datetime.fromisoformat(trade_date).date()
        closed_at = datetime(
            target_day.year,
            target_day.month,
            target_day.day,
            close_hour,
            close_minute,
            tzinfo=WIB,
        )
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=WIB)
    except (OSError, TypeError, ValueError) as exc:
        return False, f"METADATA_INVALID:{type(exc).__name__}"
    if datetime.now(WIB) < closed_at:
        return False, "TARGET_SESSION_NOT_CLOSED"
    if modified_at < closed_at:
        return False, "FILE_WRITTEN_BEFORE_MARKET_CLOSE"

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = {
                str(column).strip().lower().replace("_", " "): column
                for column in (reader.fieldnames or [])
            }
            aliases = {
                "date": ("date", "datetime", "timestamp", "index"),
                "open": ("open",),
                "high": ("high",),
                "low": ("low",),
                "close": ("close",),
                "volume": ("volume",),
            }
            selected = {
                field: next(
                    (columns[name] for name in names if name in columns),
                    None,
                )
                for field, names in aliases.items()
            }
            if any(selected[field] is None for field in REQUIRED_CANDLE_FIELDS):
                return False, "SCHEMA_INVALID"

            matched: list[dict[str, str]] = []
            for row in reader:
                date_column = selected["date"]
                if str(row.get(date_column, "")).strip()[:10] == trade_date:
                    matched.append(row)
    except (OSError, UnicodeError, csv.Error) as exc:
        return False, f"FILE_UNREADABLE:{type(exc).__name__}"

    if len(matched) != 1:
        return False, "TARGET_CANDLE_MISSING_OR_DUPLICATE"
    row = matched[0]
    symbol_column = columns.get("symbol")
    if symbol_column and _normalized_symbol(row.get(symbol_column)) != symbol:
        return False, "SYMBOL_MISMATCH"
    for field in REQUIRED_CANDLE_FIELDS[1:]:
        value = str(row.get(selected[field], "")).strip()
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, f"{field.upper()}_INVALID"
        if number != number:
            return False, f"{field.upper()}_INVALID"
    return True, "VALID_CLOSED_CANDLE"


def local_portfolio_history_current(
    historical_dir: Path,
    symbols: list[str],
    *,
    trade_date: str,
    market_close: str,
) -> tuple[bool, dict[str, str]]:
    """Prove every OPEN symbol already has the requested closed daily candle."""
    evidence: dict[str, str] = {}
    for raw_symbol in symbols:
        symbol = _normalized_symbol(raw_symbol)
        path = _history_file(historical_dir, symbol)
        if path is None:
            evidence[symbol] = "FILE_MISSING"
            continue
        valid, reason = _valid_closed_candle(
            path,
            symbol=symbol,
            trade_date=trade_date,
            market_close=market_close,
        )
        evidence[symbol] = reason
        if not valid:
            continue
    return bool(symbols) and all(
        reason == "VALID_CLOSED_CANDLE" for reason in evidence.values()
    ), evidence


def probe_yahoo_runtime(timeout_seconds: int = YAHOO_IMPORT_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Fail fast when the local yfinance/protobuf runtime is broken or hangs.

    Portfolio Management may safely continue with the last valid local OHLCV
    after this preflight returns a failure.  The shared historical downloader
    itself is intentionally not modified.
    """
    command = [
        sys.executable,
        "-c",
        (
            "import yfinance as yf; "
            "import google.protobuf as pb; "
            "print('OK|' + str(getattr(yf, '__version__', '?')) + '|' + str(getattr(pb, '__version__', '?')))"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(int(timeout_seconds), 1),
        )
    except subprocess.TimeoutExpired:
        return False, f"dependency import timeout >{max(int(timeout_seconds), 1)}s"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = output[-1].strip() if output else f"exit code {completed.returncode}"
        return False, detail[:300]
    return True, (completed.stdout or "OK").strip().splitlines()[-1]


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

    print(
        f"PORTFOLIO HISTORY REFRESH: {len(symbols)} OPEN symbol(s) | "
        f"{','.join(symbols)}",
        flush=True,
    )

    historical_dir = resolve(paths.get("historical_dir", "data/output/historical/by_symbol"))
    market_close = str(freshness.get("market_close", "16:15"))
    already_current, current_evidence = local_portfolio_history_current(
        historical_dir,
        symbols,
        trade_date=args.trade_date,
        market_close=market_close,
    )
    if already_current:
        print(
            f"PORTFOLIO HISTORY REFRESH: SKIPPED_ALREADY_CURRENT | "
            f"{len(current_evidence)} symbol(s) valid through {args.trade_date}",
            flush=True,
        )
        return 0

    runtime_ok, runtime_detail = probe_yahoo_runtime()
    if not runtime_ok:
        print(
            f"[WARNING] Portfolio OHLCV refresh skipped: Yahoo runtime unavailable ({runtime_detail}).",
            flush=True,
        )
        print(
            "[ACTION] RUN_SDE.bat > Maintenance > Repair Yahoo/YFinance runtime.",
            flush=True,
        )
        print(
            "[SAFE FALLBACK] Position Management may continue with last valid local OHLCV; stale-data rules remain active.",
            flush=True,
        )
        return YAHOO_RUNTIME_FAILURE_EXIT

    symbol_file = resolve("data/state/portfolio/OPEN_PORTFOLIO_SYMBOLS.csv")
    write_symbols(symbol_file, symbols)

    historical_root = historical_dir.parent
    manifest_dir = resolve(paths.get("manifest_dir", "data/output/manifests"))
    downloader = resolve(paths.get("historical_downloader", "modules/historical_downloader/historical_downloader.py"))
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
