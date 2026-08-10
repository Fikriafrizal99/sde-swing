#!/usr/bin/env python3
"""Stockbit SDE Historical Downloader.

V1.2 keeps closed-candle validation and run manifests around the Yahoo
download flow. Partial daily candles may be archived in CSV files, but they are
not counted as valid data refreshes unless the operator explicitly allows that
data quality mode.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import (  # noqa: E402
    atomic_csv,
    dataframe_hash,
    ensure_dir,
    file_sha256,
    iso_now,
    latest_closed_trading_date,
    make_run_id,
    write_dict_rows_csv,
    write_json,
)
from modules.market_calendar.idx_calendar import is_idx_trading_day  # noqa: E402

try:
    import yfinance as yf
except ImportError:
    yf = None


REQUIRED_CANDLE_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]
FULL_BACKFILL = "FULL_BACKFILL"
MISSING_ONLY = "MISSING_ONLY"
REPAIR_OVERLAP = "REPAIR_OVERLAP"
ALREADY_CURRENT = "ALREADY_CURRENT"
# Compatibility aliases for callers/tests that still import the V1.2 names.
INCREMENTAL_UPDATE = MISSING_ONLY
SKIP_ALREADY_CURRENT = ALREADY_CURRENT
DEFAULT_EXCLUDED_SYMBOLS = {"IHSG", "BRENT", "OIL", "XAU"}

SYMBOL_STATUSES = {
    "UPDATED_VALID",
    "UNCHANGED_ALREADY_CURRENT",
    "PARTIAL_CANDLE_IGNORED",
    "KEPT_EXISTING",
    "FAILED",
    "NO_DATA",
}


@dataclass
class RefreshPlan:
    symbol: str
    ticker: str
    destination: Path
    existing: pd.DataFrame
    existing_schema_valid: bool
    local_latest_valid_date: str
    expected_closed_date: str
    refresh_action: str
    download_start_date: str = ""
    download_end_date: str = ""
    rows_before: int = 0
    local_modified_at: str = ""
    warning: str = ""
    canonical_file_path: str = ""
    file_exists: bool = False
    first_date: str = ""
    missing_market_sessions: int = 0
    duplicate_dates: int = 0
    internal_gaps: int = 0
    first_internal_gap: str = ""
    first_missing_session: str = ""
    refresh_reason: str = ""
    history_sanitized: bool = False


@dataclass
class FetchPayload:
    fresh: pd.DataFrame
    provider_status: str
    network_request_performed: bool
    live_response_received: bool
    retry_count: int = 0
    message: str = ""


@dataclass
class DownloadResult:
    symbol: str
    ticker: str
    status: str
    refresh_action: str = ""
    local_latest_valid_date: str = ""
    download_start_date: str = ""
    download_end_date: str = ""
    rows_before: int = 0
    rows_downloaded: int = 0
    rows_after: int = 0
    first_date: str = ""
    last_date: str = ""
    latest_date_before: str = ""
    latest_date_after: str = ""
    latest_valid_close_date: str = ""
    latest_partial_date: str = ""
    expected_closed_date: str = ""
    provider_status: str = ""
    network_request_performed: bool = False
    live_response_received: bool = False
    retry_count: int = 0
    warning: str = ""
    message: str = ""
    canonical_file_path: str = ""
    file_exists: bool = False
    missing_market_sessions: int = 0
    duplicate_dates: int = 0
    internal_gaps: int = 0
    first_missing_session: str = ""
    refresh_reason: str = ""
    rows_inserted: int = 0
    rows_updated: int = 0
    file_written: bool = False
    file_unchanged: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download historical OHLCV saham IDX")
    parser.add_argument("input_csv", help="CSV normalized hasil Stockbit preprocessor")
    parser.add_argument("--output", default="historical_output", help="Folder hasil")
    parser.add_argument("--period", default="2y", help="Yahoo period, contoh: 6mo, 1y, 2y, 5y, max")
    parser.add_argument("--interval", default="1d", help="Interval, default 1d")
    parser.add_argument("--start", default=None, help="Tanggal awal YYYY-MM-DD; menggantikan --period")
    parser.add_argument("--end", default=None, help="Tanggal akhir YYYY-MM-DD (exclusive di Yahoo)")
    parser.add_argument("--symbols", default=None, help="Batasi simbol, pisahkan koma: BBCA,TLKM,ASII")
    parser.add_argument(
        "--exclude-symbols",
        default=",".join(sorted(DEFAULT_EXCLUDED_SYMBOLS)),
        help="Simbol non-equity yang tidak diproses downloader saham",
    )
    parser.add_argument("--limit", type=int, default=None, help="Batasi jumlah simbol untuk tes")
    parser.add_argument("--pause", type=float, default=0.35, help="Jeda antar simbol dalam detik")
    parser.add_argument("--retries", type=int, default=3, help="Jumlah percobaan per simbol")
    parser.add_argument("--force", action="store_true", help="Compatibility mode: sama dengan --full-backfill")
    parser.add_argument("--force-refresh", action="store_true", help="Compatibility alias untuk --repair")
    parser.add_argument("--repair", action="store_true", help="Jalankan repair overlap per simbol")
    parser.add_argument("--full-backfill", action="store_true", help="Unduh ulang full range; tidak aktif secara default")
    parser.add_argument("--skip-existing", action="store_true", help="Compatibility mode: jangan refresh file yang sudah ada")
    parser.add_argument("--repair-overlap-sessions", type=int, default=5, help="Jumlah sesi bursa untuk repair overlap")
    parser.add_argument("--incremental-overlap-days", type=int, default=5, help=argparse.SUPPRESS)
    parser.add_argument("--batch-enabled", action="store_true", help="Aktifkan batch Yahoo download untuk simbol yang perlu update")
    parser.add_argument("--no-batch", dest="batch_enabled", action="store_false", help="Matikan batch Yahoo download")
    parser.add_argument("--batch-size", type=int, default=50, help="Jumlah simbol per batch Yahoo")
    parser.add_argument("--max-workers", type=int, default=4, help="Batas worker/thread Yahoo batch")
    parser.add_argument("--request-delay-seconds", type=float, default=1.0, help="Jeda antar batch/request")
    parser.add_argument("--max-retries", type=int, default=None, help="Alias configurable untuk --retries")
    parser.add_argument("--run-id", default=None, help="Run ID pipeline Swing")
    parser.add_argument("--manifest-dir", default=None, help="Folder manifest per-run")
    parser.add_argument("--daily-candle-policy", default="LAST_CLOSED_CANDLE", choices=["LAST_CLOSED_CANDLE"])
    parser.add_argument("--allow-partial-daily-candle", action="store_true")
    parser.add_argument("--yahoo-failure-policy", default="STOP", choices=["STOP", "USE_LAST_VALID"])
    parser.add_argument("--interactive", action="store_true", help="Tampilkan menu fallback jika Yahoo tidak fresh")
    parser.add_argument("--market-close", default="16:15", help="Jam penutupan candle harian, HH:MM lokal")
    parser.add_argument("--after-midnight-cutoff", default="06:00", help="Cutoff setelah tengah malam, HH:MM lokal")
    parser.add_argument("--evaluation-datetime", default="", help="Override waktu evaluasi ISO untuk replay/test deterministik")
    parser.add_argument("--market-holiday", action="append", default=[], help="Tanggal libur bursa YYYY-MM-DD; bisa diulang")
    parser.add_argument("--special-trading-day", action="append", default=[], help="Tanggal perdagangan khusus YYYY-MM-DD; bisa diulang")
    parser.add_argument("--data-source", choices=["LIVE", "FIXTURE"], default="LIVE")
    parser.add_argument("--test-fixture", action="store_true", help="Gunakan fixture lokal; hanya untuk test")
    parser.add_argument("--fixture-dir", default="", help="Folder fixture OHLCV per simbol; hanya untuk --test-fixture")
    parser.set_defaults(batch_enabled=False)
    return parser.parse_args()


def clean_symbol(value: object) -> str:
    text = str(value).strip().upper()
    if not text or text == "NAN":
        return ""
    return text.split()[0].replace(".JK", "")


def load_symbols(
    path: Path,
    selected: str | None,
    limit: int | None,
    excluded: str | None = None,
) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Input tidak ditemukan: {path}")

    df = pd.read_csv(path, dtype=str)

    if "Symbol" not in df.columns:
        raise ValueError("Kolom 'Symbol' tidak ditemukan pada input CSV")

    symbol_col = df.loc[:, "Symbol"]

    if isinstance(symbol_col, pd.DataFrame):
        print(
            "WARNING: Ditemukan beberapa kolom bernama 'Symbol'. "
            "Menggunakan kolom pertama."
        )
        symbol_col = symbol_col.iloc[:, 0]

    symbols = [clean_symbol(x) for x in symbol_col]
    symbols = list(dict.fromkeys(x for x in symbols if x))
    excluded_set = {clean_symbol(x) for x in str(excluded or "").split(",") if clean_symbol(x)}
    symbols = [x for x in symbols if x not in excluded_set]

    if selected:
        selected_set = {clean_symbol(x) for x in selected.split(",") if clean_symbol(x)}
        symbols = [x for x in symbols if x in selected_set]

    if limit is not None:
        symbols = symbols[:limit]

    return symbols


def normalize_history(df: pd.DataFrame, symbol: str, ticker: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Symbol", "Ticker", "Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"])

    out = df.copy().reset_index(drop=False)
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] if isinstance(c, tuple) else c for c in out.columns]

    mapping = {}
    has_date_like = any(str(col).strip().lower().replace("_", " ") in {"date", "datetime", "timestamp"} for col in out.columns)
    for col in out.columns:
        key = str(col).strip().lower().replace("_", " ")
        if key in {"date", "datetime", "timestamp", "index"}:
            if key != "index" or not has_date_like:
                mapping[col] = "Date"
        elif key == "open":
            mapping[col] = "Open"
        elif key == "high":
            mapping[col] = "High"
        elif key == "low":
            mapping[col] = "Low"
        elif key == "close":
            mapping[col] = "Close"
        elif key in {"adj close", "adjclose"}:
            mapping[col] = "Adj Close"
        elif key == "volume":
            mapping[col] = "Volume"
    out = out.rename(columns=mapping)
    if "Date" not in out.columns:
        out = out.rename(columns={out.columns[0]: "Date"})

    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if col not in out.columns:
            out[col] = pd.NA

    keep = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
    out = out[keep]
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce", utc=True).dt.tz_convert(None)
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Date"]).sort_values("Date")
    out.insert(0, "Ticker", ticker)
    out.insert(0, "Symbol", symbol)
    return out


def read_existing(path: Path, symbol: str) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return normalize_history(pd.read_csv(path, low_memory=False), symbol, f"{symbol}.JK")
    except Exception:
        return pd.DataFrame()


def raw_has_required_schema(raw: pd.DataFrame) -> bool:
    normalized = {str(col).strip().lower().replace("_", " ") for col in raw.columns}
    aliases = {
        "date": {"date", "datetime", "timestamp", "index"},
        "open": {"open"},
        "high": {"high"},
        "low": {"low"},
        "close": {"close"},
        "volume": {"volume"},
    }
    return all(bool(normalized.intersection(names)) for names in aliases.values())


def inspect_existing(path: Path, symbol: str) -> tuple[pd.DataFrame, bool, str, str]:
    """Return normalized local history plus schema/currentness metadata."""
    if not path.exists():
        return pd.DataFrame(), False, "", "file_missing"
    if path.stat().st_size == 0:
        return pd.DataFrame(), False, "", "file_empty"
    modified_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    try:
        raw = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        return pd.DataFrame(), False, modified_at, f"file_unreadable: {exc}"

    schema_valid = raw_has_required_schema(raw)
    existing = normalize_history(raw, symbol, f"{symbol}.JK")
    if not schema_valid:
        return existing, False, modified_at, "schema_invalid"
    if not latest_date(existing, valid_only=True):
        return existing, False, modified_at, "no_valid_candle"
    return existing, True, modified_at, ""


def closed_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    for col in REQUIRED_CANDLE_COLUMNS:
        if col not in work.columns:
            return pd.DataFrame()
    return work.dropna(subset=REQUIRED_CANDLE_COLUMNS)


def latest_date(df: pd.DataFrame, valid_only: bool = False) -> str:
    if df.empty:
        return ""
    work = closed_rows(df) if valid_only else df
    if work.empty:
        return ""
    parsed = pd.to_datetime(work["Date"], errors="coerce")
    if parsed.dropna().empty:
        return ""
    return parsed.max().date().isoformat()


def latest_partial_date(df: pd.DataFrame) -> str:
    if df.empty or "Date" not in df.columns:
        return ""
    work = df.copy()
    for col in REQUIRED_CANDLE_COLUMNS:
        if col not in work.columns:
            work[col] = pd.NA
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    partial = work[work["Date"].notna() & work[REQUIRED_CANDLE_COLUMNS].isna().any(axis=1)]
    if partial.empty:
        return ""
    return partial["Date"].max().date().isoformat()


def trim_future_history(df: pd.DataFrame, expected_closed: date) -> tuple[pd.DataFrame, bool]:
    """Exclude candles newer than the stage's as-of date.

    Yahoo can return an in-progress daily row before the IDX close.  Once that
    row is written locally, comparing only the latest date makes subsequent
    runs incorrectly classify the file as current.  The downloader therefore
    treats the expected closed date as a hard as-of boundary and removes any
    future rows before planning or writing the history.
    """
    if df is None or df.empty or "Date" not in df.columns:
        return df, False
    parsed = pd.to_datetime(df["Date"], errors="coerce")
    cutoff = pd.Timestamp(expected_closed)
    keep = parsed.isna() | (parsed.dt.normalize() <= cutoff)
    if bool(keep.all()):
        return df, False
    return df.loc[keep].copy().reset_index(drop=True), True


def current_candle_written_before_close(
    local_latest_valid_date: str,
    expected_closed: date,
    local_modified_at: str,
    market_close: str,
) -> bool:
    """Detect a current-session row that was persisted before market close."""
    if local_latest_valid_date != expected_closed.isoformat() or not local_modified_at:
        return False
    try:
        modified = datetime.fromisoformat(local_modified_at)
        hour, minute = (int(part) for part in str(market_close).split(":", 1))
        close = dt_time(hour, minute)
    except (TypeError, ValueError):
        return False
    return modified.date() == expected_closed and modified.time() < close


def latest_session_revalidation_needed(
    local_latest_valid_date: str,
    expected_closed: date,
    evaluation_datetime: str,
    market_close: str,
) -> bool:
    """Re-check today's closed Yahoo candle even when the local date already matches.

    Daily OHLCV can change during the session while keeping the same Date.  A
    post-close run therefore must not treat a matching local date as proof that
    the row itself is current.  Before market close this stays disabled, so an
    in-progress daily candle is never promoted to the closed-candle baseline.
    """
    if local_latest_valid_date != expected_closed.isoformat():
        return False
    try:
        evaluated = (
            datetime.fromisoformat(str(evaluation_datetime))
            if str(evaluation_datetime or "").strip()
            else datetime.now().astimezone()
        )
        hour, minute = (int(part) for part in str(market_close).split(":", 1))
        close = dt_time(hour, minute)
        evaluated_time = evaluated.time().replace(tzinfo=None)
    except (TypeError, ValueError):
        return False
    return evaluated.date() == expected_closed and evaluated_time >= close


def parse_date_text(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def incremental_start_date(local_latest_valid_date: str, overlap_days: int) -> str:
    """Deprecated calendar-day overlap helper kept for compatibility."""
    parsed = parse_date_text(local_latest_valid_date)
    if parsed is None:
        return ""
    return (parsed - timedelta(days=max(int(overlap_days or 0), 0))).isoformat()


def trading_sessions_between(
    start_exclusive: date,
    end_inclusive: date,
    holidays: Iterable[str] = (),
    special_trading_days: Iterable[str] = (),
) -> list[date]:
    sessions: list[date] = []
    probe = start_exclusive + timedelta(days=1)
    while probe <= end_inclusive:
        if is_idx_trading_day(probe, holidays, special_trading_days):
            sessions.append(probe)
        probe += timedelta(days=1)
    return sessions


def repair_start_date(
    local_latest_valid_date: str,
    overlap_sessions: int,
    holidays: Iterable[str] = (),
    special_trading_days: Iterable[str] = (),
) -> str:
    parsed = parse_date_text(local_latest_valid_date)
    if parsed is None:
        return ""
    remaining = max(int(overlap_sessions or 0), 0)
    probe = parsed
    while remaining > 0:
        probe -= timedelta(days=1)
        if is_idx_trading_day(probe, holidays, special_trading_days):
            remaining -= 1
    return probe.isoformat()


def history_integrity(
    existing: pd.DataFrame,
    expected_closed: date,
    holidays: Iterable[str] = (),
    special_trading_days: Iterable[str] = (),
    lookback_sessions: int = 30,
) -> dict[str, Any]:
    if existing.empty or "Date" not in existing.columns:
        return {"first_date": "", "duplicate_dates": 0, "internal_gaps": [], "invalid_latest_candle": False}

    parsed_all = pd.to_datetime(existing["Date"], errors="coerce").dropna().dt.date
    valid = closed_rows(existing)
    parsed_valid = pd.to_datetime(valid["Date"], errors="coerce").dropna().dt.date if not valid.empty else pd.Series(dtype=object)
    if parsed_valid.empty:
        return {
            "first_date": "",
            "duplicate_dates": int(parsed_all.duplicated().sum()),
            "internal_gaps": [],
            "invalid_latest_candle": bool(len(parsed_all)),
        }

    unique_valid = sorted(set(parsed_valid))
    latest_valid = unique_valid[-1]
    earliest_probe = latest_valid
    remaining = max(int(lookback_sessions or 1), 1)
    while remaining > 0 and earliest_probe > unique_valid[0]:
        earliest_probe -= timedelta(days=1)
        if is_idx_trading_day(earliest_probe, holidays, special_trading_days):
            remaining -= 1
    expected_internal = trading_sessions_between(
        earliest_probe - timedelta(days=1),
        min(latest_valid, expected_closed),
        holidays,
        special_trading_days,
    )
    valid_set = set(unique_valid)
    gaps = [day for day in expected_internal if day not in valid_set]
    latest_any = max(parsed_all) if len(parsed_all) else None
    return {
        "first_date": unique_valid[0].isoformat(),
        "duplicate_dates": int(parsed_all.duplicated().sum()),
        "internal_gaps": gaps,
        "invalid_latest_candle": bool(latest_any and latest_any > latest_valid),
    }


def classify_refresh_action(
    existing_schema_valid: bool,
    local_latest_valid_date: str,
    expected_closed: date,
    force_refresh: bool = False,
    full_backfill: bool = False,
    needs_repair: bool = False,
) -> str:
    if full_backfill:
        return FULL_BACKFILL
    if not existing_schema_valid or not local_latest_valid_date:
        return FULL_BACKFILL
    if force_refresh or needs_repair:
        return REPAIR_OVERLAP
    if local_latest_valid_date >= expected_closed.isoformat():
        return ALREADY_CURRENT
    return MISSING_ONLY


def build_refresh_plan(symbol: str, destination: Path, args: argparse.Namespace, expected_closed: date) -> RefreshPlan:
    existing, schema_valid, modified_at, warning = inspect_existing(destination, symbol)
    existing, history_sanitized = trim_future_history(existing, expected_closed)
    if history_sanitized:
        warning = "; ".join(
            item for item in (warning, f"future_candles_ignored_after_{expected_closed.isoformat()}") if item
        )
    local_latest = latest_date(existing, valid_only=True)
    current_candle_preclose = current_candle_written_before_close(
        local_latest,
        expected_closed,
        modified_at,
        str(getattr(args, "market_close", "16:15") or "16:15"),
    )
    latest_session_revalidation = latest_session_revalidation_needed(
        local_latest,
        expected_closed,
        str(getattr(args, "evaluation_datetime", "") or ""),
        str(getattr(args, "market_close", "16:15") or "16:15"),
    )
    if current_candle_preclose:
        warning = "; ".join(
            item for item in (warning, "current_candle_written_before_market_close") if item
        )
    holidays = list(getattr(args, "market_holiday", []) or [])
    special_trading_days = list(getattr(args, "special_trading_day", []) or [])
    overlap_sessions = int(
        getattr(args, "repair_overlap_sessions", getattr(args, "incremental_overlap_days", 5)) or 0
    )
    integrity = history_integrity(
        existing,
        expected_closed,
        holidays,
        special_trading_days,
        lookback_sessions=max(overlap_sessions * 6, 30),
    )
    missing_sessions = (
        trading_sessions_between(
            parse_date_text(local_latest),
            expected_closed,
            holidays,
            special_trading_days,
        )
        if parse_date_text(local_latest)
        else []
    )
    needs_repair = bool(
        integrity["duplicate_dates"]
        or integrity["internal_gaps"]
        or integrity["invalid_latest_candle"]
        or current_candle_preclose
        or latest_session_revalidation
    )
    explicit_repair = bool(getattr(args, "repair", False) or getattr(args, "force_refresh", False))
    action = classify_refresh_action(
        schema_valid,
        local_latest,
        expected_closed,
        force_refresh=explicit_repair,
        full_backfill=bool(getattr(args, "full_backfill", False)),
        needs_repair=needs_repair,
    )
    download_start = ""
    download_end = getattr(args, "end", None) or (expected_closed + timedelta(days=1)).isoformat()
    first_missing = missing_sessions[0].isoformat() if missing_sessions else ""
    if action == MISSING_ONLY:
        download_start = first_missing
    elif action == REPAIR_OVERLAP:
        same_session_only = bool(
            latest_session_revalidation
            and not explicit_repair
            and not integrity["duplicate_dates"]
            and not integrity["internal_gaps"]
            and not integrity["invalid_latest_candle"]
        )
        download_start = (
            expected_closed.isoformat()
            if same_session_only
            else repair_start_date(local_latest, overlap_sessions, holidays, special_trading_days)
        )
        if integrity["internal_gaps"]:
            download_start = min(download_start, integrity["internal_gaps"][0].isoformat()) if download_start else integrity["internal_gaps"][0].isoformat()
    elif action == FULL_BACKFILL:
        download_start = getattr(args, "start", None) or ""
    else:
        download_end = ""

    if action == FULL_BACKFILL:
        reason = "EXPLICIT_FULL_BACKFILL" if bool(getattr(args, "full_backfill", False)) else (warning or "LOCAL_HISTORY_INVALID").upper().replace(" ", "_")
    elif explicit_repair:
        reason = "EXPLICIT_REPAIR"
    elif integrity["invalid_latest_candle"]:
        reason = "INVALID_LATEST_CANDLE"
    elif current_candle_preclose:
        reason = "CURRENT_CANDLE_WRITTEN_BEFORE_MARKET_CLOSE"
    elif latest_session_revalidation:
        reason = "LATEST_SESSION_REVALIDATION"
    elif integrity["duplicate_dates"]:
        reason = "DUPLICATE_DATES"
    elif integrity["internal_gaps"]:
        reason = "INTERNAL_GAP"
    elif action == MISSING_ONLY:
        reason = "MISSING_MARKET_SESSIONS"
    else:
        reason = "LOCAL_ALREADY_CURRENT"

    return RefreshPlan(
        symbol=symbol,
        ticker=f"{symbol}.JK",
        destination=destination,
        existing=existing,
        existing_schema_valid=schema_valid,
        local_latest_valid_date=local_latest,
        expected_closed_date=expected_closed.isoformat(),
        refresh_action=action,
        download_start_date=download_start,
        download_end_date=download_end,
        rows_before=int(len(existing)),
        local_modified_at=modified_at,
        warning=warning,
        canonical_file_path=str(destination.resolve()),
        file_exists=destination.exists(),
        first_date=str(integrity["first_date"]),
        missing_market_sessions=len(missing_sessions),
        duplicate_dates=int(integrity["duplicate_dates"]),
        internal_gaps=len(integrity["internal_gaps"]),
        first_internal_gap=integrity["internal_gaps"][0].isoformat() if integrity["internal_gaps"] else "",
        first_missing_session=first_missing,
        refresh_reason=reason,
        history_sanitized=history_sanitized,
    )


def chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    step = max(int(size or 1), 1)
    for index in range(0, len(items), step):
        yield items[index:index + step]


def merge_history(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    frames = [x for x in (existing, fresh) if x is not None and not x.empty]
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["Date"] = pd.to_datetime(combined["Date"], errors="coerce")
    combined = combined.dropna(subset=["Date"])
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if col in combined:
            combined[col] = pd.to_numeric(combined[col], errors="coerce")
    valid_rank = combined[REQUIRED_CANDLE_COLUMNS].notna().all(axis=1).astype(int)
    combined["_valid_rank"] = valid_rank
    combined = combined.sort_values(["Date", "_valid_rank"])
    combined = combined.drop_duplicates("Date", keep="last").drop(columns=["_valid_rank"])
    return combined.sort_values("Date").reset_index(drop=True)


def canonical_history_for_hash(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    columns = ["Symbol", "Ticker", "Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
    work = df.copy()
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    work = work.dropna(subset=["Date"]).drop_duplicates("Date", keep="last").sort_values("Date")
    return work[[column for column in columns if column in work.columns]].reset_index(drop=True)


def histories_equal(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    return dataframe_hash(canonical_history_for_hash(left)) == dataframe_hash(canonical_history_for_hash(right))


def should_write_history(plan: RefreshPlan, combined: pd.DataFrame) -> bool:
    if combined.empty:
        return False
    if plan.history_sanitized:
        return True
    return plan.refresh_action != ALREADY_CURRENT and not histories_equal(plan.existing, combined)


def history_change_counts(existing: pd.DataFrame, fresh: pd.DataFrame) -> tuple[int, int]:
    if fresh is None or fresh.empty:
        return 0, 0
    old = canonical_history_for_hash(existing)
    new = canonical_history_for_hash(fresh)
    if new.empty:
        return 0, 0
    old_by_date = {str(row["Date"]): row for row in old.to_dict(orient="records")} if not old.empty else {}
    inserted = 0
    updated = 0
    for row in new.to_dict(orient="records"):
        day = str(row["Date"])
        previous = old_by_date.get(day)
        if previous is None:
            inserted += 1
            continue
        comparable_columns = [column for column in ("Open", "High", "Low", "Close", "Adj Close", "Volume") if column in row]
        if any(not pd.isna(row.get(column)) and row.get(column) != previous.get(column) for column in comparable_columns):
            updated += 1
    return inserted, updated


def build_yahoo_kwargs(
    tickers: str | list[str],
    args: argparse.Namespace,
    start: str = "",
    end: str = "",
    period: str | None = None,
    batch: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "tickers": tickers,
        "interval": args.interval,
        "progress": False,
        "threads": max(int(args.max_workers or 1), 1) if batch else False,
        "timeout": 20,
        "auto_adjust": False,
    }
    if batch:
        kwargs["group_by"] = "ticker"
    if start:
        kwargs["start"] = start
        if end:
            kwargs["end"] = end
    else:
        kwargs["period"] = period or args.period
        if end:
            kwargs["end"] = end
    return kwargs


def fetch_one(
    symbol: str,
    args: argparse.Namespace,
    start: str = "",
    end: str = "",
    period: str | None = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, str, bool, bool, int]:
    if yf is None:
        return pd.DataFrame(), "Dependency yfinance belum terpasang. Jalankan: pip install -r requirements.txt", False, False, 0
    ticker = f"{symbol}.JK"
    last_error = ""
    request_performed = False
    retry_count = 0

    for attempt in range(1, args.retries + 1):
        try:
            if verbose:
                print(
                    f"[DOWNLOAD] {symbol} ({ticker}) "
                    f"attempt {attempt}/{args.retries}...",
                    flush=True,
                )

            kwargs = build_yahoo_kwargs(ticker, args, start=start or args.start or "", end=end or args.end or "", period=period)
            request_performed = True
            raw = yf.download(**kwargs)
            data = normalize_history(raw, symbol, ticker)

            if data.empty:
                raise RuntimeError("Yahoo mengembalikan data kosong")

            if verbose:
                print(
                    f"[OK] {symbol}: {len(data)} baris, "
                    f"latest_valid={latest_date(data, valid_only=True) or 'NONE'}",
                    flush=True,
                )
            return data, "success", request_performed, True, retry_count

        except Exception as exc:
            last_error = str(exc)
            retry_count = attempt
            if verbose:
                print(
                    f"[RETRY/FAIL] {symbol} "
                    f"attempt {attempt}/{args.retries}: {last_error}",
                    flush=True,
                )
            if attempt < args.retries:
                time.sleep(min(2 ** attempt, 8))

    return pd.DataFrame(), last_error, request_performed, False, retry_count


def fetch_fixture_one(symbol: str, args: argparse.Namespace) -> tuple[pd.DataFrame, str, bool, bool, int]:
    fixture_dir = Path(args.fixture_dir).expanduser()
    if not fixture_dir.exists():
        return pd.DataFrame(), f"fixture-dir tidak ditemukan: {fixture_dir}", False, False, 0
    ticker = f"{symbol}.JK"
    direct = fixture_dir / f"{symbol}.csv"
    combined = fixture_dir / "historical_ohlcv_combined.csv"
    try:
        if direct.exists():
            raw = pd.read_csv(direct, low_memory=False)
        elif combined.exists():
            all_rows = pd.read_csv(combined, low_memory=False)
            symbol_col = next((c for c in all_rows.columns if str(c).strip().lower() in {"symbol", "ticker", "emiten"}), None)
            if not symbol_col:
                return pd.DataFrame(), "fixture combined tidak memiliki kolom Symbol", False, False, 0
            raw = all_rows[all_rows[symbol_col].map(clean_symbol).eq(symbol)].copy()
        else:
            return pd.DataFrame(), f"fixture simbol tidak ditemukan: {direct}", False, False, 0
        data = normalize_history(raw, symbol, ticker)
        if data.empty:
            return pd.DataFrame(), "fixture kosong/tidak valid", False, False, 0
        print(f"[FIXTURE] {symbol}: {len(data)} baris", flush=True)
        return data, "fixture", False, False, 0
    except Exception as exc:
        return pd.DataFrame(), str(exc), False, False, 0


def split_batch_history(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    if raw is None or raw.empty:
        return {}

    output: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        levels = []
        for level in range(raw.columns.nlevels):
            levels.append({str(value) for value in raw.columns.get_level_values(level)})
        for symbol in symbols:
            ticker = f"{symbol}.JK"
            one = pd.DataFrame()
            if ticker in levels[0] or symbol in levels[0]:
                label = ticker if ticker in levels[0] else symbol
                one = raw.xs(label, axis=1, level=0, drop_level=True)
            elif len(levels) > 1 and (ticker in levels[1] or symbol in levels[1]):
                label = ticker if ticker in levels[1] else symbol
                one = raw.xs(label, axis=1, level=1, drop_level=True)
            if not one.empty:
                data = normalize_history(one, symbol, ticker)
                if not data.empty:
                    output[symbol] = data
    elif len(symbols) == 1:
        symbol = symbols[0]
        data = normalize_history(raw, symbol, f"{symbol}.JK")
        if not data.empty:
            output[symbol] = data
    return output


def fetch_batch(
    symbols: list[str],
    args: argparse.Namespace,
    start: str = "",
    end: str = "",
    period: str | None = None,
) -> tuple[dict[str, pd.DataFrame], str, bool, bool, int, int, str]:
    if yf is None:
        return {}, "Dependency yfinance belum terpasang. Jalankan: pip install -r requirements.txt", False, False, 0, 0, ""
    if not symbols:
        return {}, "empty batch", False, False, 0, 0, "empty batch"

    tickers = [f"{symbol}.JK" for symbol in symbols]
    last_error = ""
    request_performed = False
    batch_request_count = 0
    retry_count = 0
    for attempt in range(1, args.retries + 1):
        try:
            batch_request_count += 1
            request_performed = True
            raw = yf.download(**build_yahoo_kwargs(
                " ".join(tickers),
                args,
                start=start,
                end=end,
                period=period,
                batch=True,
            ))
            data_by_symbol = split_batch_history(raw, symbols)
            if not data_by_symbol:
                raise RuntimeError("Yahoo batch mengembalikan data kosong")
            return data_by_symbol, "success", request_performed, True, retry_count, batch_request_count, ""
        except Exception as exc:
            last_error = str(exc)
            retry_count = attempt
            if attempt < args.retries:
                time.sleep(min(2 ** attempt, 8))

    return {}, "failed", request_performed, False, retry_count, batch_request_count, last_error


def fetch_live_group_with_fallback(
    plans: list[RefreshPlan],
    args: argparse.Namespace,
    start: str,
    end: str,
    period: str | None = None,
) -> tuple[dict[str, FetchPayload], int]:
    if not plans:
        return {}, 0
    if len(plans) == 1:
        plan = plans[0]
        fresh, provider_status, request_performed, live_received, retry_count = fetch_one(
            plan.symbol,
            args,
            start=start,
            end=end,
            period=period,
            verbose=True,
        )
        return {
            plan.symbol: FetchPayload(
                fresh=fresh,
                provider_status=provider_status,
                network_request_performed=request_performed,
                live_response_received=live_received,
                retry_count=retry_count,
                message="" if provider_status == "success" else provider_status,
            )
        }, 0

    symbols = [plan.symbol for plan in plans]
    data_by_symbol, provider_status, request_performed, live_received, retry_count, batch_requests, message = fetch_batch(
        symbols,
        args,
        start=start,
        end=end,
        period=period,
    )
    payloads: dict[str, FetchPayload] = {}
    if provider_status == "success":
        for plan in plans:
            data = data_by_symbol.get(plan.symbol, pd.DataFrame())
            if not data.empty:
                payloads[plan.symbol] = FetchPayload(
                    fresh=data,
                    provider_status="success",
                    network_request_performed=request_performed,
                    live_response_received=live_received,
                    retry_count=retry_count,
                )
        missing = [plan for plan in plans if plan.symbol not in payloads]
        if not missing:
            return payloads, batch_requests
        if len(missing) == len(plans) and len(plans) <= 2:
            for plan in missing:
                fresh, one_status, one_request, one_live, one_retry = fetch_one(
                    plan.symbol,
                    args,
                    start=start,
                    end=end,
                    period=period,
                    verbose=True,
                )
                payloads[plan.symbol] = FetchPayload(
                    fresh=fresh,
                    provider_status=one_status,
                    network_request_performed=one_request,
                    live_response_received=one_live,
                    retry_count=one_retry,
                    message="" if one_status == "success" else one_status,
                )
            return payloads, batch_requests
        fallback_payloads, fallback_batches = fetch_live_group_with_fallback(missing, args, start, end, period)
        payloads.update(fallback_payloads)
        return payloads, batch_requests + fallback_batches

    if len(plans) <= 2:
        for plan in plans:
            fresh, one_status, one_request, one_live, one_retry = fetch_one(
                plan.symbol,
                args,
                start=start,
                end=end,
                period=period,
                verbose=True,
            )
            payloads[plan.symbol] = FetchPayload(
                fresh=fresh,
                provider_status=one_status,
                network_request_performed=one_request,
                live_response_received=one_live,
                retry_count=one_retry,
                message="" if one_status == "success" else one_status,
            )
        return payloads, batch_requests

    midpoint = max(len(plans) // 2, 1)
    left_payloads, left_batches = fetch_live_group_with_fallback(plans[:midpoint], args, start, end, period)
    right_payloads, right_batches = fetch_live_group_with_fallback(plans[midpoint:], args, start, end, period)
    payloads.update(left_payloads)
    payloads.update(right_payloads)
    if message:
        logging.warning("Batch gagal dan dipecah: %s", message)
    return payloads, batch_requests + left_batches + right_batches


def group_plans_by_request(plans: list[RefreshPlan]) -> dict[tuple[str, str, str, str], list[RefreshPlan]]:
    grouped: dict[tuple[str, str, str, str], list[RefreshPlan]] = {}
    for plan in plans:
        if plan.refresh_action == ALREADY_CURRENT:
            continue
        key = (plan.refresh_action, plan.download_start_date, plan.download_end_date, plan.refresh_reason)
        grouped.setdefault(key, []).append(plan)
    return grouped


def fetch_live_for_plans(plans: list[RefreshPlan], args: argparse.Namespace) -> tuple[dict[str, FetchPayload], int]:
    pending = [plan for plan in plans if plan.refresh_action != SKIP_ALREADY_CURRENT]
    payloads: dict[str, FetchPayload] = {}
    batch_request_count = 0
    if not pending:
        return payloads, batch_request_count

    if not args.batch_enabled or len(pending) == 1:
        for plan in pending:
            fresh, status, request_performed, live_received, retry_count = fetch_one(
                plan.symbol,
                args,
                start=plan.download_start_date,
                end=plan.download_end_date,
                period=args.period,
                verbose=True,
            )
            payloads[plan.symbol] = FetchPayload(
                fresh=fresh,
                provider_status=status,
                network_request_performed=request_performed,
                live_response_received=live_received,
                retry_count=retry_count,
                message="" if status == "success" else status,
            )
            time.sleep(max(args.pause, 0))
        return payloads, batch_request_count

    grouped = group_plans_by_request(pending)

    batch_index = 0
    total_batches = sum(len(list(chunked(group_plans, args.batch_size))) for group_plans in grouped.values())
    for (action, start, end, reason), group_plans in grouped.items():
        for chunk in chunked(group_plans, args.batch_size):
            batch_index += 1
            print(
                f"\nBatch {batch_index}/{total_batches}\n"
                f"Required start : {start or 'period:' + args.period}\n"
                f"Expected end   : {end or '-'}\n"
                f"Symbols        : {len(chunk)}\n"
                f"Refresh mode   : {action}\n"
                f"Reason         : {reason}",
                flush=True,
            )
            group_payloads, group_batch_count = fetch_live_group_with_fallback(chunk, args, start, end, args.period)
            payloads.update(group_payloads)
            batch_request_count += group_batch_count
            if args.request_delay_seconds > 0 and batch_index < total_batches:
                time.sleep(args.request_delay_seconds)

    return payloads, batch_request_count


def choose_fallback_interactively() -> str:
    print("\nYahoo refresh tidak menghasilkan candle valid terbaru.")
    print("[1] Retry Yahoo refresh")
    print("[2] Gunakan last valid historical data")
    print("[3] Pilih historical file secara manual")
    print("[4] Batalkan pipeline")
    return input("Pilihan: ").strip()


def build_result(
    symbol: str,
    existing: pd.DataFrame,
    fresh: pd.DataFrame,
    combined: pd.DataFrame,
    expected_closed: date,
    provider_status: str,
    network_request_performed: bool,
    live_response_received: bool,
    allow_partial: bool,
    message: str = "",
    refresh_action: str = "",
    download_start_date: str = "",
    download_end_date: str = "",
    retry_count: int = 0,
    warning: str = "",
    canonical_file_path: str = "",
    file_exists: bool = False,
    missing_market_sessions: int = 0,
    duplicate_dates: int = 0,
    internal_gaps: int = 0,
    first_missing_session: str = "",
    refresh_reason: str = "",
) -> DownloadResult:
    latest_before = latest_date(existing, valid_only=True)
    latest_valid_after = latest_date(combined, valid_only=True)
    latest_any_after = latest_date(combined, valid_only=False)
    partial_after = latest_partial_date(fresh)
    expected_text = expected_closed.isoformat()
    rows_inserted, rows_updated = history_change_counts(existing, fresh)
    content_changed = bool(rows_inserted or rows_updated or latest_valid_after != latest_before)

    status = "NO_DATA"
    if provider_status != "success" and combined.empty:
        status = "FAILED"
    elif not latest_valid_after:
        status = "NO_DATA"
    elif latest_valid_after >= expected_text:
        status = "UPDATED_VALID" if content_changed else "UNCHANGED_ALREADY_CURRENT"
    elif partial_after and partial_after >= expected_text and not allow_partial:
        status = "PARTIAL_CANDLE_IGNORED"
    elif provider_status != "success":
        status = "KEPT_EXISTING"
    else:
        status = "KEPT_EXISTING"

    if status not in SYMBOL_STATUSES:
        status = "FAILED"

    return DownloadResult(
        symbol=symbol,
        ticker=f"{symbol}.JK",
        status=status,
        refresh_action=refresh_action,
        local_latest_valid_date=latest_before,
        download_start_date=download_start_date,
        download_end_date=download_end_date,
        rows_before=int(len(existing)),
        rows_downloaded=int(len(fresh)) if fresh is not None else 0,
        rows_after=int(len(combined)),
        first_date=str(pd.to_datetime(combined["Date"]).min().date()) if not combined.empty else "",
        last_date=latest_any_after,
        latest_date_before=latest_before,
        latest_date_after=latest_any_after,
        latest_valid_close_date=latest_valid_after,
        latest_partial_date=partial_after,
        expected_closed_date=expected_text,
        provider_status=provider_status,
        network_request_performed=network_request_performed,
        live_response_received=live_response_received,
        retry_count=int(retry_count),
        warning=warning,
        message=message,
        canonical_file_path=canonical_file_path,
        file_exists=file_exists,
        missing_market_sessions=int(missing_market_sessions),
        duplicate_dates=int(duplicate_dates),
        internal_gaps=int(internal_gaps),
        first_missing_session=first_missing_session,
        refresh_reason=refresh_reason,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
    )


def summarize_manifest(
    args: argparse.Namespace,
    input_path: Path,
    output: Path,
    results: list[DownloadResult],
    expected_closed: date,
    fallback_used: bool,
    accepted_stale: bool,
    warning: str,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    network_request_batch_count: int,
) -> dict:
    status_df = pd.DataFrame(asdict(x) for x in results)
    latest_valid = ""
    if not status_df.empty and "latest_valid_close_date" in status_df:
        parsed = pd.to_datetime(status_df["latest_valid_close_date"], errors="coerce")
        valid_parsed = parsed.dropna()
        latest_valid = valid_parsed.max().date().isoformat() if not valid_parsed.empty else ""

    failed_symbols = status_df.loc[status_df["status"].eq("FAILED"), "symbol"].tolist() if not status_df.empty else []
    partial_symbols = status_df.loc[status_df["status"].eq("PARTIAL_CANDLE_IGNORED"), "symbol"].tolist() if not status_df.empty else []
    updated = int(status_df["status"].eq("UPDATED_VALID").sum()) if not status_df.empty else 0
    unchanged = int(status_df["status"].isin(["UNCHANGED_ALREADY_CURRENT", "KEPT_EXISTING"]).sum()) if not status_df.empty else 0
    partial = int(status_df["status"].eq("PARTIAL_CANDLE_IGNORED").sum()) if not status_df.empty else 0
    failed = int(status_df["status"].eq("FAILED").sum()) if not status_df.empty else 0
    no_data = int(status_df["status"].eq("NO_DATA").sum()) if not status_df.empty else 0
    full_backfill = int(status_df["refresh_action"].eq(FULL_BACKFILL).sum()) if not status_df.empty and "refresh_action" in status_df else 0
    incremental = int(status_df["refresh_action"].eq(MISSING_ONLY).sum()) if not status_df.empty and "refresh_action" in status_df else 0
    repair = int(status_df["refresh_action"].eq(REPAIR_OVERLAP).sum()) if not status_df.empty and "refresh_action" in status_df else 0
    already_current = int(status_df["refresh_action"].eq(ALREADY_CURRENT).sum()) if not status_df.empty and "refresh_action" in status_df else 0
    skipped = already_current
    retry_total = int(pd.to_numeric(status_df["retry_count"], errors="coerce").fillna(0).sum()) if not status_df.empty and "retry_count" in status_df else 0
    network_request_symbol_count = int(status_df["network_request_performed"].fillna(False).astype(bool).sum()) if not status_df.empty and "network_request_performed" in status_df else 0
    if len(results) and already_current == len(results):
        refresh_mode = ALREADY_CURRENT
    elif len(results) and full_backfill == len(results):
        refresh_mode = FULL_BACKFILL
    elif len(results) and repair == len(results):
        refresh_mode = REPAIR_OVERLAP
    elif len(results) and incremental == len(results):
        refresh_mode = MISSING_ONLY
    else:
        refresh_mode = "MIXED_PER_SYMBOL"

    request_groups: dict[tuple[str, str, str, str], int] = {}
    for result in results:
        if result.refresh_action == ALREADY_CURRENT:
            continue
        key = (result.refresh_action, result.download_start_date, result.download_end_date, result.refresh_reason)
        request_groups[key] = request_groups.get(key, 0) + 1
    request_plan = [
        {
            "refresh_mode": key[0],
            "request_start": key[1] or None,
            "request_end": key[2] or None,
            "reason": key[3],
            "symbols": count,
        }
        for key, count in sorted(request_groups.items())
    ]
    symbol_plans = []
    for result in results:
        canonical_status = (
            "ALREADY_CURRENT" if result.status == "UNCHANGED_ALREADY_CURRENT"
            else "UPDATED" if result.status == "UPDATED_VALID"
            else result.status
        )
        symbol_plans.append({
            "symbol": result.symbol,
            "canonical_file_path": result.canonical_file_path,
            "file_exists": result.file_exists,
            "row_count": result.rows_before,
            "first_date": result.first_date,
            "local_last_date_before": result.local_latest_valid_date or result.latest_date_before,
            "expected_closed_date": result.expected_closed_date,
            "missing_market_sessions": result.missing_market_sessions,
            "duplicate_dates": result.duplicate_dates,
            "internal_gaps": result.internal_gaps,
            "first_missing_session": result.first_missing_session or None,
            "request_start": result.download_start_date or None,
            "request_end": result.download_end_date or None,
            "refresh_mode": result.refresh_action,
            "reason": result.refresh_reason,
            "downloaded_rows": result.rows_downloaded,
            "inserted_rows": result.rows_inserted,
            "updated_rows": result.rows_updated,
            "local_last_date_after": result.latest_valid_close_date,
            "file_written": result.file_written,
            "status": canonical_status,
        })

    provider_mode = "LIVE" if str(args.data_source).upper() == "LIVE" else "FIXTURE"
    network_request_performed = bool(status_df["network_request_performed"].fillna(False).astype(bool).any()) if not status_df.empty and "network_request_performed" in status_df else False
    live_response_received = bool(status_df["live_response_received"].fillna(False).astype(bool).any()) if not status_df.empty and "live_response_received" in status_df else False

    if failed and not fallback_used:
        candle_status = "PROVIDER_FAILED"
        quality = "PROVIDER_FAILED"
    elif partial and latest_valid < expected_closed.isoformat() and not fallback_used and not args.allow_partial_daily_candle:
        candle_status = "PARTIAL_DAILY_CANDLE"
        quality = "PARTIAL_CANDLE"
    elif fallback_used:
        candle_status = "LAST_VALID_USED"
        quality = "STALE_ACCEPTED" if accepted_stale else "INVALID"
    elif latest_valid and latest_valid >= expected_closed.isoformat():
        candle_status = "VALID_CLOSED_CANDLE"
        quality = "VALID"
    elif latest_valid:
        candle_status = "NO_NEW_VALID_CANDLE"
        quality = "STALE_ACCEPTED" if accepted_stale else "INVALID"
    else:
        candle_status = "PROVIDER_FAILED"
        quality = "PROVIDER_FAILED"

    refresh_status = "SUCCESS" if candle_status == "VALID_CLOSED_CANDLE" else candle_status

    return {
        "Run_ID": args.run_id,
        "Run_Timestamp": iso_now(),
        "Provider": "Yahoo Finance",
        "Provider_Mode": provider_mode,
        "Data_Source": "LIVE_YAHOO" if provider_mode == "LIVE" else "OFFLINE_FIXTURE",
        "Mode": "PRODUCTION" if provider_mode == "LIVE" else "TEST",
        "Network_Request_Performed": network_request_performed,
        "Live_Response_Received": live_response_received,
        "Refresh_Mode": refresh_mode,
        "Full_Backfill_Count": full_backfill,
        "Incremental_Update_Count": incremental,
        "Missing_Only_Count": incremental,
        "Repair_Overlap_Count": repair,
        "Already_Current_Count": already_current,
        "Skipped_Count": skipped,
        "Retry_Count": retry_total,
        "Failed_Count": failed,
        "Network_Request_Symbol_Count": network_request_symbol_count,
        "Network_Request_Batch_Count": int(network_request_batch_count),
        "Requests_Planned": len(request_plan),
        "Request_Groups": request_plan,
        "Batch_Size": int(args.batch_size),
        "Worker_Count": int(args.max_workers),
        "Started_At": started_at,
        "Finished_At": finished_at,
        "Duration_Seconds": round(float(duration_seconds), 3),
        "Universe_Source": str(input_path),
        "Universe_Hash": file_sha256(input_path, short=True),
        "Excluded_Symbols": sorted({clean_symbol(x) for x in str(args.exclude_symbols or "").split(",") if clean_symbol(x)}),
        "Symbol_Total": len(results),
        "Requested_Start_Date": args.start or "",
        "Requested_End_Date": args.end or (expected_closed + timedelta(days=1)).isoformat(),
        "Latest_Expected_Trading_Date": expected_closed.isoformat(),
        "Latest_Closed_Candle_Date": expected_closed.isoformat(),
        "Latest_Actual_Valid_Date": latest_valid,
        "Historical_Output_Path": str(output / "historical_ohlcv_combined.csv"),
        "Rows_Before": int(sum(x.rows_before for x in results)),
        "Rows_Downloaded": int(sum(x.rows_downloaded for x in results)),
        "Rows_Inserted": int(sum(x.rows_inserted for x in results)),
        "Rows_Updated": int(sum(x.rows_updated for x in results)),
        "Rows_After": int(sum(x.rows_after for x in results)),
        "Files_Unchanged": int(sum(bool(x.file_unchanged) for x in results)),
        "Latest_Date_Before": max([x.latest_date_before for x in results if x.latest_date_before] or [""]),
        "Latest_Date_After": max([x.latest_date_after for x in results if x.latest_date_after] or [""]),
        "Latest_Valid_Close_Date": latest_valid,
        "Symbol_Updated_Valid": updated,
        "Symbol_Unchanged": unchanged,
        "Symbol_Partial": partial,
        "Symbol_Failed": failed,
        "Symbol_No_Data": no_data,
        "Failed_Symbols": failed_symbols,
        "Partial_Symbols": partial_symbols,
        "Fallback_Used": bool(fallback_used),
        "Refresh_Status": refresh_status,
        "Candle_Status": candle_status,
        "Refresh_Detail_Status": candle_status,
        "Data_Quality_Status": quality,
        "Warning": warning,
        "Daily_Candle_Policy": args.daily_candle_policy,
        "Allow_Partial_Daily_Candle": bool(args.allow_partial_daily_candle),
        "Yahoo_Failure_Policy": args.yahoo_failure_policy,
        "Symbol_Plans": symbol_plans,
    }


def symbol_status_rows(results: list[DownloadResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        canonical_status = (
            "ALREADY_CURRENT" if result.status == "UNCHANGED_ALREADY_CURRENT"
            else "UPDATED" if result.status == "UPDATED_VALID"
            else result.status
        )
        rows.append({
            "Symbol": result.symbol,
            "Canonical_File_Path": result.canonical_file_path,
            "File_Exists": result.file_exists,
            "Row_Count": result.rows_before,
            "First_Date": result.first_date,
            "Local_Latest_Valid_Date": result.local_latest_valid_date or result.latest_date_before,
            "Expected_Closed_Date": result.expected_closed_date,
            "Missing_Market_Sessions": result.missing_market_sessions,
            "Duplicate_Dates": result.duplicate_dates,
            "Internal_Gaps": result.internal_gaps,
            "First_Missing_Session": result.first_missing_session,
            "Refresh_Action": result.refresh_action,
            "Refresh_Reason": result.refresh_reason,
            "Download_Start_Date": result.download_start_date,
            "Download_End_Date": result.download_end_date,
            "Rows_Before": result.rows_before,
            "Rows_Downloaded": result.rows_downloaded,
            "Rows_Inserted": result.rows_inserted,
            "Rows_Updated": result.rows_updated,
            "Rows_After": result.rows_after,
            "File_Written": result.file_written,
            "File_Unchanged": result.file_unchanged,
            "Network_Request_Performed": result.network_request_performed,
            "Retry_Count": result.retry_count,
            "Status": canonical_status,
            "Warning": result.warning or result.message,
        })
    return rows


def main() -> int:
    args = parse_args()
    started_at = iso_now()
    started_monotonic = time.perf_counter()
    if args.max_retries is not None:
        args.retries = args.max_retries
    args.retries = max(int(args.retries or 1), 1)
    args.batch_size = max(int(args.batch_size or 1), 1)
    args.max_workers = max(int(args.max_workers or 1), 1)
    args.incremental_overlap_days = max(int(args.incremental_overlap_days or 0), 0)
    args.repair_overlap_sessions = max(int(args.repair_overlap_sessions or 0), 0)
    if args.force:
        args.full_backfill = True
    if args.test_fixture:
        args.data_source = "FIXTURE"
    args.run_id = args.run_id or make_run_id()
    input_path = Path(args.input_csv).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    per_symbol = output / "by_symbol"
    logs = output / "logs"
    manifest_dir = Path(args.manifest_dir).expanduser().resolve() if args.manifest_dir else output.parent / "manifests"
    ensure_dir(per_symbol)
    ensure_dir(logs)
    ensure_dir(manifest_dir)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    evaluation_at = datetime.fromisoformat(args.evaluation_datetime) if args.evaluation_datetime else None
    expected_closed = latest_closed_trading_date(
        at=evaluation_at,
        holidays=args.market_holiday,
        market_close=args.market_close,
        after_midnight_cutoff=args.after_midnight_cutoff,
    )

    try:
        symbols = load_symbols(input_path, args.symbols, args.limit, args.exclude_symbols)
    except Exception as exc:
        logging.error("Gagal membaca input: %s", exc)
        return 2

    if not symbols:
        logging.error("Tidak ada simbol untuk diproses")
        return 2

    data_source_label = "LIVE_YAHOO" if args.data_source == "LIVE" else "OFFLINE_FIXTURE"
    run_mode_label = "PRODUCTION" if args.data_source == "LIVE" else "TEST"
    print(f"Data Source : {data_source_label}")
    print(f"Mode        : {run_mode_label}")
    logging.info("Memproses %d simbol; expected closed candle=%s", len(symbols), expected_closed)
    results: list[DownloadResult] = []
    combined_parts: list[pd.DataFrame] = []
    accepted_stale = False
    fallback_used = False
    warning = ""

    plans = [build_refresh_plan(symbol, per_symbol / f"{symbol}.csv", args, expected_closed) for symbol in symbols]
    if args.skip_existing and not args.force_refresh and not args.full_backfill:
        for plan in plans:
            if plan.destination.exists() and not plan.existing.empty:
                plan.refresh_action = SKIP_ALREADY_CURRENT
                plan.download_start_date = ""
                plan.download_end_date = ""
                plan.warning = "; ".join(x for x in [plan.warning, "skip-existing compatibility mode"] if x)

    full_backfill_count = sum(1 for plan in plans if plan.refresh_action == FULL_BACKFILL)
    missing_only_count = sum(1 for plan in plans if plan.refresh_action == MISSING_ONLY)
    repair_count = sum(1 for plan in plans if plan.refresh_action == REPAIR_OVERLAP)
    already_current_count = sum(1 for plan in plans if plan.refresh_action == ALREADY_CURRENT)
    retry_candidates = sum(
        1 for plan in plans
        if any(plan.warning.startswith(prefix) for prefix in ("file_missing", "file_empty", "file_unreadable", "schema_invalid", "no_valid_candle"))
    )
    missing_one = sum(1 for plan in plans if plan.missing_market_sessions == 1)
    missing_two_to_five = sum(1 for plan in plans if 2 <= plan.missing_market_sessions <= 5)
    request_groups = {
        (plan.refresh_action, plan.download_start_date, plan.download_end_date, plan.refresh_reason)
        for plan in plans if plan.refresh_action != ALREADY_CURRENT
    }

    print("\nYahoo Refresh - INCREMENTAL MISSING-ONLY")
    print(f"Universe             : {len(plans)} simbol")
    print(f"Already current      : {already_current_count}")
    print(f"Missing 1 session    : {missing_one}")
    print(f"Missing 2-5 sessions : {missing_two_to_five}")
    print(f"Need missing-only    : {missing_only_count}")
    print(f"Need repair          : {repair_count}")
    print(f"Need full backfill   : {full_backfill_count}")
    print(f"Retry/new/invalid    : {retry_candidates}")
    print(f"Requests planned     : {len(request_groups)}")
    print(f"Batch enabled        : {bool(args.batch_enabled and args.data_source == 'LIVE')}")

    fetched: dict[str, FetchPayload] = {}
    network_request_batch_count = 0
    if args.data_source == "FIXTURE":
        for plan in plans:
            if plan.refresh_action == SKIP_ALREADY_CURRENT:
                continue
            fresh, provider_status, request_performed, live_received, retry_count = fetch_fixture_one(plan.symbol, args)
            fetched[plan.symbol] = FetchPayload(
                fresh=fresh,
                provider_status=provider_status,
                network_request_performed=request_performed,
                live_response_received=live_received,
                retry_count=retry_count,
                message="" if provider_status == "fixture" else provider_status,
            )
    else:
        fetched, network_request_batch_count = fetch_live_for_plans(plans, args)

    for index, plan in enumerate(plans, start=1):
        if plan.refresh_action == SKIP_ALREADY_CURRENT:
            payload = FetchPayload(
                fresh=pd.DataFrame(),
                provider_status="skipped_current",
                network_request_performed=False,
                live_response_received=False,
                retry_count=0,
                message=plan.warning or "already current; no network request",
            )
            combined = plan.existing
        else:
            payload = fetched.get(
                plan.symbol,
                FetchPayload(pd.DataFrame(), "failed", False, False, 0, "download payload missing"),
            )
            base_existing = pd.DataFrame() if plan.refresh_action == FULL_BACKFILL else plan.existing
            combined = merge_history(base_existing, payload.fresh)

        provider_status_for_result = "success" if payload.provider_status in {"success", "fixture", "skipped_current"} else "failed"
        symbol_warning = "; ".join(x for x in [plan.warning, payload.message] if x)
        result = build_result(
            plan.symbol,
            plan.existing,
            payload.fresh,
            combined,
            expected_closed,
            provider_status_for_result,
            payload.network_request_performed,
            payload.live_response_received,
            args.allow_partial_daily_candle,
            "" if payload.provider_status in {"success", "fixture", "skipped_current"} else payload.provider_status,
            refresh_action=plan.refresh_action,
            download_start_date=plan.download_start_date,
            download_end_date=plan.download_end_date,
            retry_count=payload.retry_count,
            warning=symbol_warning,
            canonical_file_path=plan.canonical_file_path,
            file_exists=plan.file_exists,
            missing_market_sessions=plan.missing_market_sessions,
            duplicate_dates=plan.duplicate_dates,
            internal_gaps=plan.internal_gaps,
            first_missing_session=plan.first_missing_session,
            refresh_reason=plan.refresh_reason,
        )

        if not combined.empty:
            if should_write_history(plan, combined):
                atomic_csv(combined, plan.destination)
                result.file_written = True
            else:
                result.file_unchanged = True
            combined_parts.append(combined)
        results.append(result)
        if result.status == "UPDATED_VALID":
            logging.info("[%d/%d] %s: updated valid -> %s", index, len(symbols), plan.symbol, result.latest_valid_close_date)
        elif result.status == "UNCHANGED_ALREADY_CURRENT":
            logging.info("[%d/%d] %s: sudah current -> %s", index, len(symbols), plan.symbol, result.latest_valid_close_date)
        elif result.status == "PARTIAL_CANDLE_IGNORED":
            logging.warning("[%d/%d] %s: partial candle ignored latest_partial=%s latest_valid=%s", index, len(symbols), plan.symbol, result.latest_partial_date, result.latest_valid_close_date)
        else:
            logging.warning("[%d/%d] %s: %s %s", index, len(symbols), plan.symbol, result.status, result.message or result.warning)

    print(
        "Summary              : "
        f"updated={sum(1 for x in results if x.status == 'UPDATED_VALID')}, "
        f"skipped={already_current_count}, "
        f"failed={sum(1 for x in results if x.status == 'FAILED')}, "
        f"downloaded={sum(x.rows_downloaded for x in results)}, "
        f"inserted={sum(x.rows_inserted for x in results)}, "
        f"updated_rows={sum(x.rows_updated for x in results)}, "
        f"unchanged_files={sum(bool(x.file_unchanged) for x in results)}, "
        f"network_symbols={sum(1 for x in results if x.network_request_performed)}, "
        f"batch_requests={network_request_batch_count}",
        flush=True,
    )

    if combined_parts:
        combined_all = pd.concat(combined_parts, ignore_index=True)
        combined_all["Date"] = pd.to_datetime(combined_all["Date"], errors="coerce")
        combined_all = combined_all.dropna(subset=["Date"]).drop_duplicates(["Symbol", "Date"], keep="last").sort_values(["Symbol", "Date"])
        atomic_csv(combined_all, output / "historical_ohlcv_combined.csv")
    else:
        combined_all = pd.DataFrame()

    result_rows = [asdict(x) for x in results]
    detail_rows = symbol_status_rows(results)
    result_df = pd.DataFrame(result_rows)
    atomic_csv(result_df, logs / "download_status.csv")
    failed = result_df[result_df["status"] == "FAILED"] if not result_df.empty else result_df
    atomic_csv(failed, logs / "failed_symbols.csv")

    def current_manifest(fallback: bool, accepted: bool, current_warning: str) -> dict:
        return summarize_manifest(
            args,
            input_path,
            output,
            results,
            expected_closed,
            fallback,
            accepted,
            current_warning,
            started_at,
            iso_now(),
            time.perf_counter() - started_monotonic,
            network_request_batch_count,
        )

    provisional = current_manifest(False, False, "")
    stale_or_invalid = provisional["Data_Quality_Status"] in {"INVALID", "PROVIDER_FAILED", "PARTIAL_CANDLE"}
    if stale_or_invalid and not args.allow_partial_daily_candle:
        if args.interactive:
            choice = choose_fallback_interactively()
            if choice == "1":
                print("Silakan jalankan ulang downloader untuk retry Yahoo refresh.")
                warning = "RETRY_REQUESTED_BY_USER"
                manifest = current_manifest(False, False, warning)
                write_json(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{args.run_id}.json", manifest)
                write_dict_rows_csv(detail_rows, manifest_dir / f"YAHOO_SYMBOL_STATUS_{args.run_id}.csv")
                return 4
            if choice == "2":
                fallback_used = True
                accepted_stale = True
                warning = "STALE_DATA_ACCEPTED_BY_USER"
            elif choice == "3":
                manual = input("Path historical manual: ").strip().strip('"')
                manual_path = Path(manual).expanduser()
                if not manual_path.exists():
                    warning = f"MANUAL_HISTORICAL_NOT_FOUND: {manual}"
                    manifest = current_manifest(False, False, warning)
                    write_json(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{args.run_id}.json", manifest)
                    write_dict_rows_csv(detail_rows, manifest_dir / f"YAHOO_SYMBOL_STATUS_{args.run_id}.csv")
                    return 5
                fallback_used = True
                accepted_stale = True
                warning = f"MANUAL_HISTORICAL_SELECTED: {manual_path}"
            else:
                warning = "PIPELINE_CANCELLED_BY_USER"
                manifest = current_manifest(False, False, warning)
                write_json(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{args.run_id}.json", manifest)
                write_dict_rows_csv(detail_rows, manifest_dir / f"YAHOO_SYMBOL_STATUS_{args.run_id}.csv")
                return 6
        elif args.yahoo_failure_policy == "USE_LAST_VALID":
            fallback_used = True
            accepted_stale = True
            warning = "STALE_DATA_ACCEPTED_BY_CONFIG"
        else:
            warning = "Yahoo refresh tidak menghasilkan candle valid terbaru; pipeline dihentikan."

    manifest = current_manifest(fallback_used, accepted_stale, warning)
    if fallback_used:
        manifest["DATA_MODE"] = "LAST_VALID"
        manifest["FALLBACK_USED"] = True
        manifest["DATA_WARNING"] = warning or "STALE_DATA_ACCEPTED"
        manifest["DATA_QUALITY_STATUS"] = "STALE_ACCEPTED"
    else:
        manifest["DATA_MODE"] = "FRESH"

    write_json(output / "manifest.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(input_path),
        "symbols_requested": len(symbols),
        "successful": int(result_df["status"].isin(["UPDATED_VALID", "UNCHANGED_ALREADY_CURRENT"]).sum()) if not result_df.empty else 0,
        "partial_ignored": int(result_df["status"].eq("PARTIAL_CANDLE_IGNORED").sum()) if not result_df.empty else 0,
        "failed": int(result_df["status"].eq("FAILED").sum()) if not result_df.empty else 0,
        "period": args.period,
        "interval": args.interval,
        "start": args.start,
        "end": args.end,
        "run_id": args.run_id,
        "data_quality_status": manifest["Data_Quality_Status"],
        "refresh_mode": manifest["Refresh_Mode"],
        "already_current": manifest["Already_Current_Count"],
        "incremental_update": manifest["Incremental_Update_Count"],
        "missing_only": manifest["Missing_Only_Count"],
        "repair_overlap": manifest["Repair_Overlap_Count"],
        "full_backfill": manifest["Full_Backfill_Count"],
        "network_request_symbol_count": manifest["Network_Request_Symbol_Count"],
        "network_request_batch_count": manifest["Network_Request_Batch_Count"],
    })
    write_json(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{args.run_id}.json", manifest)
    write_dict_rows_csv(detail_rows, manifest_dir / f"YAHOO_SYMBOL_STATUS_{args.run_id}.csv")

    logging.info(
        "Selesai. Updated=%d, unchanged=%d, partial=%d, failed=%d, quality=%s",
        manifest["Symbol_Updated_Valid"],
        manifest["Symbol_Unchanged"],
        manifest["Symbol_Partial"],
        manifest["Symbol_Failed"],
        manifest["Data_Quality_Status"],
    )

    if stale_or_invalid and not fallback_used and not args.allow_partial_daily_candle:
        logging.error(warning)
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())