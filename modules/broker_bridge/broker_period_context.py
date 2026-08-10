from __future__ import annotations

"""Broker analysis-period selection, validation, and immutable snapshots.

This module is intentionally presentation/orchestration only.  It does not
calculate Broker Score, Broker Confidence, or any final trading decision.

Important contracts:
- 1D/3D/5D mean actual IDX trading sessions ending on the Final Watchlist date.
- An aggregate 3D/5D/CUSTOM export remains one aggregate snapshot; it is never
  decomposed into synthetic daily observations.
- Every accepted export is copied into an immutable snapshot directory before
  it can become the canonical Broker Summary input.
- Historical snapshots are never deleted or merged arithmetically.
"""

import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from swing_utils import file_sha256, normalize_symbol
from modules.market_calendar.idx_calendar import is_idx_trading_day, previous_idx_trading_day


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALENDAR = PROJECT_ROOT / "config/trading_calendar.json"
DEFAULT_SNAPSHOT_ROOT = PROJECT_ROOT / "data/output/broker_snapshots"

REQUIRED_SUMMARY_COLUMNS = {
    "FROM_DATE",
    "TO_DATE",
    "EMITEN",
    "TOTAL_BUY",
    "TOTAL_SELL",
    "NET_FLOW",
    "TOP_BUYER_1",
    "TOP_SELLER_1",
    "BUYER_CONCENTRATION",
    "SELLER_CONCENTRATION",
}
FIXED_PERIOD_SESSIONS = {"1D": 1, "3D": 3, "5D": 5}
PERIOD_TYPES = ("1D", "3D", "5D", "CUSTOM")


@dataclass(frozen=True)
class BrokerPeriodSpec:
    period_type: str
    period_start: str
    period_end: str
    trading_sessions: int
    session_dates: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["session_dates"] = list(self.session_dates)
        return payload


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def load_calendar_rules(path: Path = DEFAULT_CALENDAR) -> tuple[list[str], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw_holidays = payload.get("holidays", {})
    if isinstance(raw_holidays, dict):
        holidays = [
            str(day)
            for day, detail in raw_holidays.items()
            if not isinstance(detail, dict) or bool(detail.get("holiday", True))
        ]
    else:
        holidays = [str(item) for item in raw_holidays or []]
    special = [str(item) for item in payload.get("special_trading_days", []) or []]
    return holidays, special


def trading_sessions_between(
    start: Any,
    end: Any,
    *,
    calendar_path: Path = DEFAULT_CALENDAR,
) -> list[str]:
    start_date = _as_date(start)
    end_date = _as_date(end)
    if start_date > end_date:
        raise ValueError("BROKER_PERIOD_START_AFTER_END")
    holidays, special = load_calendar_rules(calendar_path)
    sessions: list[str] = []
    probe = start_date
    while probe <= end_date:
        if is_idx_trading_day(probe, holidays=holidays, special_trading_days=special):
            sessions.append(probe.isoformat())
        probe += timedelta(days=1)
    return sessions


def fixed_period_spec(
    period_type: str,
    period_end: Any,
    *,
    calendar_path: Path = DEFAULT_CALENDAR,
) -> BrokerPeriodSpec:
    period_type = str(period_type).strip().upper()
    if period_type not in FIXED_PERIOD_SESSIONS:
        raise ValueError(f"BROKER_PERIOD_UNSUPPORTED:{period_type}")
    end_date = _as_date(period_end)
    holidays, special = load_calendar_rules(calendar_path)
    if not is_idx_trading_day(end_date, holidays=holidays, special_trading_days=special):
        raise ValueError(f"BROKER_PERIOD_END_NOT_TRADING_DAY:{end_date.isoformat()}")
    count = FIXED_PERIOD_SESSIONS[period_type]
    sessions = [end_date]
    probe = end_date
    while len(sessions) < count:
        probe = previous_idx_trading_day(
            probe,
            holidays=holidays,
            special_trading_days=special,
        )
        sessions.append(probe)
    sessions = sorted(sessions)
    return BrokerPeriodSpec(
        period_type=period_type,
        period_start=sessions[0].isoformat(),
        period_end=end_date.isoformat(),
        trading_sessions=count,
        session_dates=tuple(day.isoformat() for day in sessions),
    )


def custom_period_spec(
    period_start: Any,
    period_end: Any,
    *,
    calendar_path: Path = DEFAULT_CALENDAR,
) -> BrokerPeriodSpec:
    start_date = _as_date(period_start)
    end_date = _as_date(period_end)
    sessions = trading_sessions_between(start_date, end_date, calendar_path=calendar_path)
    if not sessions:
        raise ValueError("BROKER_CUSTOM_PERIOD_HAS_NO_TRADING_SESSION")
    if sessions[0] != start_date.isoformat():
        raise ValueError(f"BROKER_CUSTOM_START_NOT_TRADING_DAY:{start_date.isoformat()}")
    if sessions[-1] != end_date.isoformat():
        raise ValueError(f"BROKER_CUSTOM_END_NOT_TRADING_DAY:{end_date.isoformat()}")
    return BrokerPeriodSpec(
        period_type="CUSTOM",
        period_start=start_date.isoformat(),
        period_end=end_date.isoformat(),
        trading_sessions=len(sessions),
        session_dates=tuple(sessions),
    )


def expected_symbols_from_csv(path: Path) -> list[str]:
    frame = pd.read_csv(path, low_memory=False)
    column = next(
        (
            col
            for col in frame.columns
            if str(col).strip().upper() in {"SYMBOL", "EMITEN", "TICKER", "CODE"}
        ),
        None,
    )
    if column is None:
        raise RuntimeError(f"BROKER_SYMBOL_COLUMN_NOT_FOUND:{path}")
    values = [normalize_symbol(value) for value in frame[column].tolist()]
    return list(dict.fromkeys(value for value in values if value))


def inspect_summary_export(
    path: Path,
    expected_symbols: Iterable[str],
    min_coverage: float,
    spec: BrokerPeriodSpec,
) -> tuple[bool, str, dict[str, Any]]:
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        return False, f"CSV_NOT_READABLE:{type(exc).__name__}:{exc}", {}
    missing_columns = sorted(REQUIRED_SUMMARY_COLUMNS - set(frame.columns))
    if missing_columns:
        return False, f"MISSING_COLUMNS:{','.join(missing_columns)}", {}
    if frame.empty:
        return False, "EMPTY_EXPORT", {}

    from_dates = pd.to_datetime(frame["FROM_DATE"], errors="coerce").dropna()
    to_dates = pd.to_datetime(frame["TO_DATE"], errors="coerce").dropna()
    if from_dates.empty or to_dates.empty:
        return False, "INVALID_PERIOD_DATES", {}
    detected_start = from_dates.min().date().isoformat()
    detected_end = to_dates.max().date().isoformat()
    if detected_start != spec.period_start or detected_end != spec.period_end:
        return False, (
            f"PERIOD_MISMATCH:expected={spec.period_start}..{spec.period_end};"
            f"detected={detected_start}..{detected_end}"
        ), {
            "detected_start": detected_start,
            "detected_end": detected_end,
        }

    normalized = frame["EMITEN"].map(normalize_symbol)
    symbols = {value for value in normalized if value}
    expected = set(expected_symbols)
    matched = expected & symbols
    coverage = len(matched) / max(1, len(expected))
    duplicate_symbols = sorted(normalized[normalized.duplicated()].dropna().unique().tolist())
    if duplicate_symbols:
        return False, f"DUPLICATE_SYMBOLS:{','.join(duplicate_symbols)}", {
            "coverage": coverage,
            "duplicate_symbols": duplicate_symbols,
        }
    if coverage < min_coverage:
        return False, f"COVERAGE_BELOW_MINIMUM:{coverage:.4f}<{min_coverage:.4f}", {
            "coverage": coverage,
            "matched": len(matched),
            "expected": len(expected),
            "missing_symbols": sorted(expected - symbols),
        }

    info = {
        "source": str(path.resolve()),
        "source_hash": file_sha256(path),
        "rows": int(len(frame)),
        "symbol_count": len(symbols),
        "matched": len(matched),
        "expected": len(expected),
        "coverage": coverage,
        "missing_symbols": sorted(expected - symbols),
        "unexpected_symbols": sorted(symbols - expected),
        "duplicate_symbols": duplicate_symbols,
        "detected_start": detected_start,
        "detected_end": detected_end,
    }
    return True, "OK", info


def find_matching_export(
    downloads: Path,
    expected_symbols: Iterable[str],
    min_coverage: float,
    spec: BrokerPeriodSpec,
) -> tuple[Path | None, dict[str, Any], str]:
    files = sorted(
        downloads.glob("BROKER_SUMMARY_COMBINED_*.csv"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    last_message = "BROKER_EXPORT_NOT_FOUND"
    for path in files:
        valid, message, info = inspect_summary_export(path, expected_symbols, min_coverage, spec)
        if valid:
            return path, info, "OK"
        last_message = f"{path.name}:{message}"
    return None, {}, last_message


def wait_for_matching_export(
    downloads: Path,
    expected_symbols: Iterable[str],
    min_coverage: float,
    spec: BrokerPeriodSpec,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> tuple[Path, dict[str, Any]]:
    started = time.time()
    last_message = ""
    while time.time() - started <= timeout_seconds:
        path, info, message = find_matching_export(downloads, expected_symbols, min_coverage, spec)
        if path is not None:
            return path, info
        if message != last_message:
            print(f"[BROKER PERIOD] {message}", flush=True)
            last_message = message
        time.sleep(max(0.25, poll_seconds))
    raise TimeoutError(
        f"BROKER_PERIOD_EXPORT_TIMEOUT:{spec.period_type}:{spec.period_start}..{spec.period_end}"
    )


def raw_companion(summary_path: Path) -> Path | None:
    candidate = summary_path.with_name(
        summary_path.name.replace("BROKER_SUMMARY_COMBINED_", "BROKER_RAW_COMBINED_")
    )
    return candidate if candidate.exists() and candidate.stat().st_size > 0 else None


def freshness_status(snapshot_end: str, as_of: str, *, calendar_path: Path = DEFAULT_CALENDAR) -> str:
    end = _as_date(snapshot_end)
    current = _as_date(as_of)
    if end >= current:
        return "CURRENT"
    sessions = trading_sessions_between(end, current, calendar_path=calendar_path)
    age = max(0, len(sessions) - 1)
    if age <= 1:
        return "RECENT"
    if age <= 3:
        return "AGING"
    return "HISTORICAL"


def persist_snapshot(
    summary_path: Path,
    raw_path: Path | None,
    spec: BrokerPeriodSpec,
    info: dict[str, Any],
    *,
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
    selected_by: str = "FINAL_WATCHLIST",
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(
        "|".join(
            [
                spec.period_type,
                spec.period_start,
                spec.period_end,
                str(info.get("source_hash") or file_sha256(summary_path)),
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    snapshot_id = f"BROKER-{spec.period_type}-{spec.period_end.replace('-', '')}-{fingerprint}"
    root = snapshot_root / spec.period_end / snapshot_id
    root.mkdir(parents=True, exist_ok=True)
    summary_copy = root / "BROKER_SUMMARY.csv"
    if not summary_copy.exists():
        shutil.copy2(summary_path, summary_copy)
    raw_copy: Path | None = None
    if raw_path is not None:
        raw_copy = root / "BROKER_RAW.csv"
        if not raw_copy.exists():
            shutil.copy2(raw_path, raw_copy)

    manifest = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selected_by": selected_by,
        "primary_context": True,
        "historical_context_only": False,
        "broker_period_type": spec.period_type,
        "broker_period_start": spec.period_start,
        "broker_period_end": spec.period_end,
        "broker_trading_days": spec.trading_sessions,
        "broker_session_dates": list(spec.session_dates),
        "freshness_status": "CURRENT",
        "summary_source": str(summary_path.resolve()),
        "summary_source_hash": file_sha256(summary_path),
        "summary_snapshot_path": str(summary_copy.resolve()),
        "summary_snapshot_hash": file_sha256(summary_copy),
        "raw_source": str(raw_path.resolve()) if raw_path else "",
        "raw_source_hash": file_sha256(raw_path) if raw_path else "",
        "raw_snapshot_path": str(raw_copy.resolve()) if raw_copy else "",
        "raw_snapshot_hash": file_sha256(raw_copy) if raw_copy else "",
        "rows": int(info.get("rows", 0) or 0),
        "matched_symbols": int(info.get("matched", 0) or 0),
        "expected_symbols": int(info.get("expected", 0) or 0),
        "coverage_ratio": float(info.get("coverage", 0.0) or 0.0),
        "missing_symbols": list(info.get("missing_symbols", []) or []),
        "unexpected_symbols": list(info.get("unexpected_symbols", []) or []),
        "aggregate_snapshot": spec.period_type != "1D",
        "daily_history_eligible": spec.period_type == "1D",
        "scoring_adjustment_applied": False,
        "freshness_adjustment_applied": False,
        "persistence_adjustment_applied": False,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path.resolve())
    latest = snapshot_root / "latest_selected.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def list_reusable_snapshots(
    trade_date: str,
    *,
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
) -> list[dict[str, Any]]:
    root = snapshot_root / str(trade_date)
    if not root.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        summary = Path(str(payload.get("summary_snapshot_path", "")))
        if (
            payload.get("broker_period_end") == trade_date
            and payload.get("snapshot_id")
            and summary.exists()
            and summary.stat().st_size > 0
        ):
            payload["manifest_path"] = str(path.resolve())
            snapshots.append(payload)
    return snapshots


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".period_tmp")
    shutil.copy2(source, temp)
    os.replace(temp, destination)
