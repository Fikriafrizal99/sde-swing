#!/usr/bin/env python3
"""Shared helpers for SDE Swing V1.5 Professional Telegram UI.

The helpers in this file are intentionally small and side-effect free so the
existing scoring engines can keep their formulas unchanged while the pipeline
adds data freshness, lineage, and audit metadata around them.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


PACKAGE_VERSION = "1.6.2-stage2"
PIPELINE_VERSION = "SDE_SWING_V1_6_2_STAGE2_MODERATE_CALIBRATION"
DISPLAY_VERSION = "SDE Swing V1.6.2 Stage 2 Moderate Calibration"
STRATEGY_TYPE = "SWING"
VALID_DATA_QUALITY = {
    "VALID",
    "STALE_ACCEPTED",
    "PARTIAL_CANDLE",
    "BROKER_DATE_OVERRIDE",
    "PARTIAL_COVERAGE",
    "MANUAL_FILE",
    "PROVIDER_FAILED",
    "INVALID",
}


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def make_run_id(prefix: str = "SWING") -> str:
    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"{prefix}-{stamp}-{suffix}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def file_sha256(path: Path | str | None, short: bool = False) -> str:
    if path is None:
        return ""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    return digest[:16] if short else digest


def dataframe_hash(df: pd.DataFrame, columns: Iterable[str] | None = None, short: bool = False) -> str:
    if df is None or df.empty:
        digest = hashlib.sha256(b"").hexdigest()
        return digest[:16] if short else digest
    work = df.copy()
    if columns:
        selected = [c for c in columns if c in work.columns]
        if selected:
            work = work[selected]
    work = work.sort_index(axis=1)
    csv_text = work.to_csv(index=False, lineterminator="\n")
    digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
    return digest[:16] if short else digest


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper().replace(".JK", "")
    if not text or text == "NAN":
        return ""
    first = text.splitlines()[0].split()[0]
    match = re.search(r"[A-Z0-9]{2,12}", first)
    return match.group(0) if match else ""


def norm_col(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("%", " pct ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def find_col(df: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {norm_col(c): c for c in df.columns}
    for alias in aliases:
        key = norm_col(alias)
        if key in mapping:
            return mapping[key]
    return None


def require_col(df: pd.DataFrame, *aliases: str) -> str:
    col = find_col(df, *aliases)
    if col is None:
        raise ValueError(f"Kolom wajib tidak ditemukan. Salah satu dari: {aliases}")
    return col


def clean_dataframe_for_json(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    work = df.where(pd.notna(df), None)
    return work.to_dict(orient="records")


def read_csv_safely(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def atomic_csv(df: pd.DataFrame, destination: Path, **to_csv_kwargs: Any) -> None:
    ensure_dir(destination.parent)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    df.to_csv(temp, index=False, **to_csv_kwargs)
    temp.replace(destination)


def write_dict_rows_csv(rows: list[dict[str, Any]], destination: Path) -> None:
    ensure_dir(destination.parent)
    if not rows:
        destination.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_holidays(values: Iterable[str] | None) -> set[date]:
    holidays: set[date] = set()
    for value in values or []:
        try:
            holidays.add(pd.Timestamp(value).date())
        except Exception:
            continue
    return holidays


def is_business_day(day: date, holidays: set[date] | None = None) -> bool:
    return day.weekday() < 5 and day not in (holidays or set())


def previous_business_day(day: date, holidays: set[date] | None = None) -> date:
    probe = day - timedelta(days=1)
    while not is_business_day(probe, holidays):
        probe -= timedelta(days=1)
    return probe


def latest_closed_trading_date(
    at: datetime | None = None,
    holidays: Iterable[str] | None = None,
    market_close: str = "16:15",
    after_midnight_cutoff: str = "06:00",
) -> date:
    """Return the latest daily candle that should be closed for IDX swing use.

    This avoids comparing data to date.today() blindly. Before market close, or
    shortly after midnight, the latest expected closed candle is the previous
    business day.
    """
    current = at or now_local()
    holiday_set = parse_holidays(holidays)
    close_hour, close_min = [int(x) for x in market_close.split(":", 1)]
    cutoff_hour, cutoff_min = [int(x) for x in after_midnight_cutoff.split(":", 1)]
    close_time = time(close_hour, close_min)
    cutoff_time = time(cutoff_hour, cutoff_min)
    today = current.date()

    if not is_business_day(today, holiday_set):
        return previous_business_day(today, holiday_set)
    if current.time() < cutoff_time:
        return previous_business_day(today, holiday_set)
    if current.time() < close_time:
        return previous_business_day(today, holiday_set)
    return today


def csv_latest_date(path: Path, *date_aliases: str) -> str:
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:
        return ""
    col = find_col(df, *(date_aliases or ("Date",)))
    if not col:
        return ""
    parsed = pd.to_datetime(df[col], errors="coerce")
    if parsed.dropna().empty:
        return ""
    return parsed.max().date().isoformat()

