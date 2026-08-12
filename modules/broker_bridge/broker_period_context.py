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
BROKER_PERIOD_SOURCES = (
    "STOCKBIT_1D",
    "INTERNAL_DAILY_ROLLUP",
    "STOCKBIT_AGGREGATE_EXPORT",
)
ALIGNMENT_LABELS = (
    "ALIGNED_POSITIVE",
    "ALIGNED_NEGATIVE",
    "POSITIVE_DIVERGENCE",
    "NEGATIVE_DIVERGENCE",
    "INSUFFICIENT",
)


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


def period_source_for(
    period_type: str,
    *,
    internal_rollup: bool = False,
    aggregate_export: bool = False,
) -> str:
    """Return the provenance label for the selected PRIMARY context.

    The source is metadata only.  It never selects a different score formula.
    Explicit flags win so callers can distinguish an internal rollup from a
    Stockbit aggregate even when both cover the same sessions.
    """
    if internal_rollup:
        return "INTERNAL_DAILY_ROLLUP"
    if aggregate_export or str(period_type).strip().upper() != "1D":
        return "STOCKBIT_AGGREGATE_EXPORT"
    return "STOCKBIT_1D"


def session_coverage(
    expected_dates: Iterable[str],
    observed_dates: Iterable[str],
) -> dict[str, Any]:
    """Describe exact session coverage without shifting a missing date."""
    expected = [str(value)[:10] for value in expected_dates if str(value).strip()]
    observed = {str(value)[:10] for value in observed_dates if str(value).strip()}
    available = [value for value in expected if value in observed]
    missing = [value for value in expected if value not in observed]
    count = len(expected)
    ratio = len(available) / count if count else 0.0
    return {
        "broker_coverage": round(ratio, 6),
        "broker_session_coverage": round(ratio, 6),
        "broker_period_coverage": round(ratio, 6),
        "broker_coverage_text": f"{len(available)}/{count}",
        "broker_available_sessions": len(available),
        "broker_expected_sessions": count,
        "broker_missing_session_dates": missing,
        "broker_missing_sessions": missing,
        "broker_coverage_status": "COMPLETE" if count and not missing else "INCOMPLETE",
        "broker_period_complete": bool(count and not missing),
    }


def primary_context_metadata(
    spec: BrokerPeriodSpec,
    *,
    snapshot_id: str = "",
    source: str = "",
    coverage: float | None = None,
    observed_session_dates: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the stable metadata envelope carried by every PRIMARY context."""
    source_name = str(source or period_source_for(spec.period_type)).strip().upper()
    if source_name not in BROKER_PERIOD_SOURCES:
        raise ValueError(f"BROKER_PERIOD_SOURCE_UNSUPPORTED:{source_name}")
    observed = list(observed_session_dates if observed_session_dates is not None else spec.session_dates)
    metadata: dict[str, Any] = {
        "broker_period_type": spec.period_type,
        "broker_period_start": spec.period_start,
        "broker_period_end": spec.period_end,
        "broker_trading_days": int(spec.trading_sessions),
        "broker_session_dates": list(spec.session_dates),
        "broker_snapshot_id": str(snapshot_id or ""),
        "broker_period_source": source_name,
        "broker_coverage": float(coverage) if coverage is not None else 1.0,
    }
    if coverage is not None:
        metadata["broker_symbol_coverage"] = float(coverage)
    metadata.update(session_coverage(spec.session_dates, observed))
    if coverage is not None:
        # ``broker_coverage`` is the symbol/export coverage.  Session coverage
        # remains separately available so the two dimensions are not confused.
        metadata["broker_coverage"] = float(coverage)
        metadata["broker_symbol_coverage"] = float(coverage)
    return metadata


def primary_context_metadata_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize a persisted snapshot/sidecar into the PRIMARY envelope."""
    period_type = str(manifest.get("broker_period_type", "")).strip().upper()
    session_dates = [str(item)[:10] for item in manifest.get("broker_session_dates", []) or []]
    spec = BrokerPeriodSpec(
        period_type=period_type,
        period_start=str(manifest.get("broker_period_start", ""))[:10],
        period_end=str(manifest.get("broker_period_end", ""))[:10],
        trading_sessions=int(manifest.get("broker_trading_days", len(session_dates)) or 0),
        session_dates=tuple(session_dates),
    )
    metadata = primary_context_metadata(
        spec,
        snapshot_id=str(manifest.get("broker_snapshot_id") or manifest.get("snapshot_id") or ""),
        source=str(manifest.get("broker_period_source") or ""),
        coverage=(
            float(manifest.get("broker_coverage"))
            if manifest.get("broker_coverage") not in (None, "")
            else float(manifest.get("coverage_ratio", 0.0) or 0.0)
        ),
        observed_session_dates=manifest.get("observed_session_dates") or session_dates,
    )
    metadata["broker_snapshot_id"] = str(manifest.get("snapshot_id") or metadata["broker_snapshot_id"])
    metadata["broker_freshness_status"] = str(
        manifest.get("freshness_status") or manifest.get("broker_freshness_status") or "CURRENT"
    ).upper()
    if "broker_missing_sessions" in manifest:
        metadata["broker_missing_sessions"] = list(manifest.get("broker_missing_sessions") or [])
    elif "broker_missing_session_dates" in manifest:
        metadata["broker_missing_sessions"] = list(manifest.get("broker_missing_session_dates") or [])
    if "broker_period_complete" in manifest:
        metadata["broker_period_complete"] = bool(manifest.get("broker_period_complete"))
    return metadata


def today_pulse_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    pulse_date: str = "",
    snapshot_id: str = "",
    source: str = "STOCKBIT_1D",
) -> dict[str, Any]:
    """Create interpretation-only metadata from the latest real 1D rows."""
    materialized = [dict(row) for row in rows]
    dates = sorted({str(row.get("market_date", ""))[:10] for row in materialized if str(row.get("market_date", "")).strip()})
    selected_date = str(pulse_date or (dates[-1] if dates else ""))[:10]
    selected = [
        row
        for row in materialized
        if str(row.get("market_date", ""))[:10] == selected_date
        and str(row.get("source", "")).upper()
        not in {"STOCKBIT_AGGREGATE_EXPORT", "AGGREGATE", "BROKER_AGGREGATE"}
    ]
    if not selected:
        return {
            "today_pulse_available": False,
            "today_pulse_date": selected_date,
            "today_pulse_snapshot_id": str(snapshot_id or ""),
            "today_pulse_source": "" if not snapshot_id else str(source or "STOCKBIT_1D").upper(),
            "today_pulse_status": "NOT_AVAILABLE",
            "today_pulse_net_flow": 0.0,
            "today_pulse_buy_days": 0,
            "today_pulse_sell_days": 0,
            "today_pulse_direction": "INSUFFICIENT",
        }
    net = 0.0
    for row in selected:
        try:
            value = float(row.get("net_value") or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        net += value if str(row.get("side", "")).upper() == "BUY" else -abs(value)
    direction = "POSITIVE" if net > 0 else "NEGATIVE" if net < 0 else "NEUTRAL"
    return {
        "today_pulse_available": True,
        "today_pulse_date": selected_date,
        "today_pulse_snapshot_id": str(snapshot_id or ""),
        "today_pulse_source": str(source or "STOCKBIT_1D").upper(),
        "today_pulse_status": "AVAILABLE",
        "today_pulse_net_flow": round(net, 4),
        "today_pulse_buy_days": 1 if net > 0 else 0,
        "today_pulse_sell_days": 1 if net < 0 else 0,
        "today_pulse_direction": direction,
    }


def primary_pulse_alignment(
    primary_net_flow: float | None,
    pulse_net_flow: float | None,
    *,
    pulse_status: str = "AVAILABLE",
) -> str:
    """Classify PRIMARY vs TODAY PULSE for interpretation only."""
    if str(pulse_status or "").upper() not in {"AVAILABLE", "VALID", "CURRENT"}:
        return "INSUFFICIENT"
    if primary_net_flow is None or pulse_net_flow is None:
        return "INSUFFICIENT"
    try:
        primary = float(primary_net_flow)
        pulse = float(pulse_net_flow)
    except (TypeError, ValueError):
        return "INSUFFICIENT"
    if primary == 0 or pulse == 0:
        return "INSUFFICIENT"
    if primary > 0 and pulse > 0:
        return "ALIGNED_POSITIVE"
    if primary < 0 and pulse < 0:
        return "ALIGNED_NEGATIVE"
    if primary > 0 and pulse < 0:
        return "NEGATIVE_DIVERGENCE"
    if primary < 0 and pulse > 0:
        return "POSITIVE_DIVERGENCE"
    return "INSUFFICIENT"


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

    from_dates = pd.to_datetime(frame["FROM_DATE"], errors="coerce")
    to_dates = pd.to_datetime(frame["TO_DATE"], errors="coerce")
    invalid_date_rows = frame.index[from_dates.isna() | to_dates.isna()].tolist()
    if invalid_date_rows:
        return False, f"INVALID_PERIOD_DATES_ROWS:{','.join(map(str, invalid_date_rows[:20]))}", {
            "invalid_period_rows": invalid_date_rows,
        }
    if from_dates.empty or to_dates.empty:
        return False, "INVALID_PERIOD_DATES", {}

    normalized_from = from_dates.dt.strftime("%Y-%m-%d")
    normalized_to = to_dates.dt.strftime("%Y-%m-%d")
    range_mismatch = (normalized_from != spec.period_start) | (normalized_to != spec.period_end)
    if range_mismatch.any():
        mismatch_rows = frame.index[range_mismatch].tolist()
        return False, (
            f"PERIOD_MISMATCH_ROWS:expected={spec.period_start}..{spec.period_end};"
            f"rows={','.join(map(str, mismatch_rows[:20]))}"
        ), {
            "mismatch_rows": mismatch_rows,
            "detected_from_values": sorted(set(normalized_from.tolist())),
            "detected_to_values": sorted(set(normalized_to.tolist())),
        }

    normalized = frame["EMITEN"].map(normalize_symbol)
    empty_symbol_rows = frame.index[normalized.eq("")].tolist()
    if empty_symbol_rows:
        return False, f"EMPTY_SYMBOL_ROWS:{','.join(map(str, empty_symbol_rows[:20]))}", {
            "empty_symbol_rows": empty_symbol_rows,
        }
    symbols = {value for value in normalized if value}
    expected = {normalize_symbol(value) for value in expected_symbols if normalize_symbol(value)}
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

    unexpected_symbols = sorted(symbols - expected)
    if unexpected_symbols:
        return False, f"UNEXPECTED_SYMBOLS:{','.join(unexpected_symbols)}", {
            "coverage": coverage,
            "matched": len(matched),
            "expected": len(expected),
            "unexpected_symbols": unexpected_symbols,
            "missing_symbols": sorted(expected - symbols),
        }

    detected_start = spec.period_start
    detected_end = spec.period_end

    info = {
        "source": str(path.resolve()),
        "source_hash": file_sha256(path),
        "rows": int(len(frame)),
        "symbol_count": len(symbols),
        "matched": len(matched),
        "expected": len(expected),
        "coverage": coverage,
        "missing_symbols": sorted(expected - symbols),
        "unexpected_symbols": unexpected_symbols,
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
    summary_source_hash = file_sha256(summary_path)
    raw_source_hash = file_sha256(raw_path) if raw_path and raw_path.exists() else ""
    fingerprint = hashlib.sha256(
        "|".join(
            [
                spec.period_type,
                spec.period_start,
                spec.period_end,
                summary_source_hash,
                raw_source_hash or "NO_RAW",
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    snapshot_id = f"BROKER-{spec.period_type}-{spec.period_end.replace('-', '')}-{fingerprint}"
    root = snapshot_root / spec.period_end / snapshot_id
    root.mkdir(parents=True, exist_ok=True)
    existing_manifest_path = root / "manifest.json"
    if existing_manifest_path.exists():
        try:
            existing_payload = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"BROKER_SNAPSHOT_MANIFEST_UNREADABLE:{snapshot_id}") from exc
        existing_state = str(existing_payload.get("snapshot_state", "")).upper()
        legacy_committed = (
            not existing_state
            and bool(str(existing_payload.get("final_watchlist_run_id", "")).strip())
            and bool(str(existing_payload.get("activated_at", "")).strip())
        )
        if existing_state == "COMMITTED" or legacy_committed:
            reusable = [
                item
                for item in list_reusable_snapshots(spec.period_end, snapshot_root=snapshot_root)
                if item.get("snapshot_id") == snapshot_id
            ]
            if reusable:
                return reusable[0]
            raise RuntimeError(f"BROKER_SNAPSHOT_COMMITTED_INVALID:{snapshot_id}")
    summary_copy = root / "BROKER_SUMMARY.csv"
    if not summary_copy.exists():
        shutil.copy2(summary_path, summary_copy)
    elif file_sha256(summary_copy) != summary_source_hash:
        raise RuntimeError(f"BROKER_SNAPSHOT_SUMMARY_HASH_COLLISION:{snapshot_id}")
    raw_copy: Path | None = None
    if raw_path is not None and raw_source_hash:
        raw_copy = root / "BROKER_RAW.csv"
        if not raw_copy.exists():
            shutil.copy2(raw_path, raw_copy)
        elif file_sha256(raw_copy) != raw_source_hash:
            raise RuntimeError(f"BROKER_SNAPSHOT_RAW_HASH_COLLISION:{snapshot_id}")

    manifest = {
        "snapshot_id": snapshot_id,
        "broker_snapshot_id": snapshot_id,
        "snapshot_state": "PENDING",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selected_by": selected_by,
        "primary_context": True,
        "historical_context_only": False,
        "broker_period_type": spec.period_type,
        "broker_period_start": spec.period_start,
        "broker_period_end": spec.period_end,
        "broker_trading_days": spec.trading_sessions,
        "broker_session_dates": list(spec.session_dates),
        "broker_period_source": period_source_for(spec.period_type, aggregate_export=spec.period_type != "1D"),
        "freshness_status": freshness_status(spec.period_end, spec.period_end),
        "summary_source": str(summary_path.resolve()),
        "summary_source_hash": summary_source_hash,
        "source_path": str(summary_path.resolve()),
        "source_hash": summary_source_hash,
        "summary_hash": summary_source_hash,
        "summary_snapshot_path": str(summary_copy.resolve()),
        "summary_snapshot_hash": file_sha256(summary_copy),
        "raw_source": str(raw_path.resolve()) if raw_path else "",
        "raw_source_hash": raw_source_hash,
        "raw_hash": raw_source_hash,
        "raw_snapshot_path": str(raw_copy.resolve()) if raw_copy else "",
        "raw_snapshot_hash": file_sha256(raw_copy) if raw_copy else "",
        "rows": int(info.get("rows", 0) or 0),
        "matched_symbols": int(info.get("matched", 0) or 0),
        "expected_symbols": int(info.get("expected", 0) or 0),
        "coverage_ratio": float(info.get("coverage", 0.0) or 0.0),
        "broker_coverage": float(info.get("coverage", 0.0) or 0.0),
        "broker_period_coverage": 1.0,
        "broker_session_coverage": 1.0,
        "broker_coverage_text": f"{spec.trading_sessions}/{spec.trading_sessions}",
        "broker_coverage_status": "COMPLETE",
        "broker_missing_sessions": [],
        "broker_missing_session_dates": [],
        "broker_period_complete": True,
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
    return manifest


def persist_internal_rollup_snapshot(
    daily_manifest: dict[str, Any],
    spec: BrokerPeriodSpec,
    *,
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
    selected_by: str = "FINAL_WATCHLIST_INTERNAL_ROLLUP",
) -> dict[str, Any]:
    """Persist a PRIMARY manifest whose lineage is a complete real-daily rollup.

    The copied files remain the real 1D source files used by the existing
    Broker Summary/Fusion stages.  The manifest is deliberately separate from
    the 1D capture so REUSE and performance analytics can distinguish the
    selected PRIMARY horizon from the daily observation that supplied it.
    No aggregate export is synthesized and no daily rows are created here.
    """
    if str(daily_manifest.get("broker_period_type", "")).upper() not in {"1D", "1DAY", "DAY"}:
        raise ValueError("BROKER_INTERNAL_ROLLUP_REQUIRES_1D_CAPTURE")
    if str(spec.period_type).upper() == "1D":
        raise ValueError("BROKER_INTERNAL_ROLLUP_REQUIRES_MULTI_DAY")

    daily_summary = Path(str(daily_manifest.get("summary_snapshot_path", "")))
    daily_raw_text = str(daily_manifest.get("raw_snapshot_path", "")).strip()
    daily_raw = Path(daily_raw_text) if daily_raw_text else None
    if not daily_summary.exists() or daily_summary.stat().st_size <= 0:
        raise RuntimeError("BROKER_INTERNAL_ROLLUP_DAILY_SUMMARY_MISSING")
    if daily_raw is None or not daily_raw.exists() or daily_raw.stat().st_size <= 0:
        raise RuntimeError("BROKER_INTERNAL_ROLLUP_DAILY_RAW_MISSING")

    summary_hash = file_sha256(daily_summary)
    raw_hash = file_sha256(daily_raw)
    daily_snapshot_id = str(daily_manifest.get("snapshot_id") or daily_manifest.get("broker_snapshot_id") or "")
    fingerprint = hashlib.sha256(
        "|".join([
            "INTERNAL_DAILY_ROLLUP",
            spec.period_type,
            spec.period_start,
            spec.period_end,
            daily_snapshot_id,
            summary_hash,
            raw_hash,
        ]).encode("utf-8")
    ).hexdigest()[:16]
    snapshot_id = f"BROKER-{spec.period_type}-{spec.period_end.replace('-', '')}-INTERNAL-{fingerprint}"
    root = snapshot_root / spec.period_end / snapshot_id
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"BROKER_SNAPSHOT_MANIFEST_UNREADABLE:{snapshot_id}") from exc
        existing["manifest_path"] = str(manifest_path.resolve())
        return existing

    summary_copy = root / "BROKER_SUMMARY.csv"
    raw_copy = root / "BROKER_RAW.csv"
    if not summary_copy.exists():
        shutil.copy2(daily_summary, summary_copy)
    elif file_sha256(summary_copy) != summary_hash:
        raise RuntimeError(f"BROKER_SNAPSHOT_SUMMARY_HASH_COLLISION:{snapshot_id}")
    if not raw_copy.exists():
        shutil.copy2(daily_raw, raw_copy)
    elif file_sha256(raw_copy) != raw_hash:
        raise RuntimeError(f"BROKER_SNAPSHOT_RAW_HASH_COLLISION:{snapshot_id}")

    coverage = float(daily_manifest.get("coverage_ratio", daily_manifest.get("broker_coverage", 0.0)) or 0.0)
    manifest = {
        "snapshot_id": snapshot_id,
        "broker_snapshot_id": snapshot_id,
        "snapshot_state": "PENDING",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selected_by": selected_by,
        "primary_context": True,
        "historical_context_only": False,
        "broker_period_type": spec.period_type,
        "broker_period_start": spec.period_start,
        "broker_period_end": spec.period_end,
        "broker_trading_days": spec.trading_sessions,
        "broker_session_dates": list(spec.session_dates),
        "observed_session_dates": list(spec.session_dates),
        "broker_period_source": "INTERNAL_DAILY_ROLLUP",
        "freshness_status": freshness_status(spec.period_end, spec.period_end),
        "summary_source": str(daily_summary.resolve()),
        "summary_source_hash": summary_hash,
        "source_path": str(daily_summary.resolve()),
        "source_hash": summary_hash,
        "summary_hash": summary_hash,
        "summary_snapshot_path": str(summary_copy.resolve()),
        "summary_snapshot_hash": file_sha256(summary_copy),
        "raw_source": str(daily_raw.resolve()),
        "raw_source_hash": raw_hash,
        "raw_hash": raw_hash,
        "raw_snapshot_path": str(raw_copy.resolve()),
        "raw_snapshot_hash": file_sha256(raw_copy),
        "rows": int(daily_manifest.get("rows", 0) or 0),
        "matched_symbols": int(daily_manifest.get("matched_symbols", 0) or 0),
        "expected_symbols": int(daily_manifest.get("expected_symbols", 0) or 0),
        "coverage_ratio": coverage,
        "broker_coverage": coverage,
        "broker_period_coverage": 1.0,
        "broker_session_coverage": 1.0,
        "broker_coverage_text": f"{spec.trading_sessions}/{spec.trading_sessions}",
        "broker_coverage_status": "COMPLETE",
        "broker_missing_sessions": [],
        "broker_missing_session_dates": [],
        "broker_period_complete": True,
        "missing_symbols": list(daily_manifest.get("missing_symbols", []) or []),
        "unexpected_symbols": list(daily_manifest.get("unexpected_symbols", []) or []),
        "aggregate_snapshot": True,
        "daily_history_eligible": False,
        "daily_source_snapshot_id": daily_snapshot_id,
        "daily_source_manifest_path": str(daily_manifest.get("manifest_path", "")),
        "daily_source_hash": raw_hash,
        "scoring_adjustment_applied": False,
        "freshness_adjustment_applied": False,
        "persistence_adjustment_applied": False,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path.resolve())
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
        snapshot_state = str(payload.get("snapshot_state", "")).upper()
        final_run_id = str(payload.get("final_watchlist_run_id", "")).strip()
        committed_at = str(payload.get("committed_at") or payload.get("activated_at") or "").strip()
        legacy_committed = not snapshot_state and bool(final_run_id) and bool(committed_at)
        if (
            (snapshot_state != "COMMITTED" and not legacy_committed)
            or not final_run_id
            or not committed_at
            or payload.get("broker_period_end") != trade_date
            or not payload.get("snapshot_id")
        ):
            continue

        try:
            period_type = str(payload.get("broker_period_type", "")).upper()
            if period_type in FIXED_PERIOD_SESSIONS:
                expected_spec = fixed_period_spec(period_type, trade_date)
            elif period_type == "CUSTOM":
                expected_spec = custom_period_spec(payload.get("broker_period_start", ""), trade_date)
            else:
                continue
            if (
                payload.get("broker_period_start") != expected_spec.period_start
                or payload.get("broker_period_end") != expected_spec.period_end
                or int(payload.get("broker_trading_days", 0) or 0) != expected_spec.trading_sessions
                or list(payload.get("broker_session_dates", []) or []) != list(expected_spec.session_dates)
            ):
                continue
        except Exception:
            continue

        snapshot_dir = path.parent.resolve()
        summary = Path(str(payload.get("summary_snapshot_path", ""))).resolve()
        expected_summary = (snapshot_dir / "BROKER_SUMMARY.csv").resolve()
        if (
            summary != expected_summary
            or not summary.exists()
            or summary.stat().st_size <= 0
            or not str(payload.get("summary_snapshot_hash", "")).strip()
            or file_sha256(summary) != str(payload.get("summary_snapshot_hash"))
        ):
            continue

        raw_text = str(payload.get("raw_snapshot_path", "")).strip()
        if raw_text:
            raw = Path(raw_text).resolve()
            expected_raw = (snapshot_dir / "BROKER_RAW.csv").resolve()
            if (
                raw != expected_raw
                or not raw.exists()
                or raw.stat().st_size <= 0
                or not str(payload.get("raw_snapshot_hash", "")).strip()
                or file_sha256(raw) != str(payload.get("raw_snapshot_hash"))
            ):
                continue

        payload.setdefault("snapshot_state", "COMMITTED")
        payload.setdefault("committed_at", committed_at)
        payload["manifest_path"] = str(path.resolve())
        snapshots.append(payload)
    return snapshots


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".period_tmp")
    shutil.copy2(source, temp)
    os.replace(temp, destination)
