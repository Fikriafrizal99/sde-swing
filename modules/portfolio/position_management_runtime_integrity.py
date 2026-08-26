#!/usr/bin/env python3
from __future__ import annotations

"""Integrity launcher for Position Management.

This module does not change management/scoring rules.  It only guarantees that
technical evidence is evaluated as-of the requested ``analysis_date`` before
calling the existing Position Management runtime.
"""

import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from modules.portfolio import position_management_engine as base
from modules.portfolio import position_management_runtime as runtime

_ORIGINAL_ANALYZE_POSITION = runtime.analyze_position
_ORIGINAL_TECHNICAL_SNAPSHOT = base.technical_snapshot


def technical_snapshot_as_of(
    historical_dir: Path,
    symbol: str,
    buy_date: str,
    analysis_date: str,
) -> dict[str, Any]:
    """Run the existing technical snapshot on candles capped at analysis_date."""
    path = base.history_path(historical_dir, symbol)
    if path is None:
        return {
            "status": "MISSING",
            "reason": "HISTORICAL_FILE_NOT_FOUND",
            "symbol": symbol,
        }

    cutoff = pd.to_datetime(analysis_date, errors="coerce")
    if pd.isna(cutoff):
        return {
            "status": "INVALID",
            "reason": "INVALID_ANALYSIS_DATE",
            "symbol": symbol,
        }
    cutoff = cutoff.normalize()

    try:
        raw = pd.read_csv(path, low_memory=False)
        normalized = base.normalize_columns(raw)
    except Exception as exc:
        return {
            "status": "INVALID",
            "reason": f"TECHNICAL_READ_FAILED:{type(exc).__name__}:{exc}",
            "symbol": symbol,
        }

    if "Date" not in normalized.columns:
        return {
            "status": "INVALID",
            "reason": "TECHNICAL_DATE_COLUMN_MISSING",
            "symbol": symbol,
        }

    dates = pd.to_datetime(normalized["Date"], errors="coerce").dt.normalize()
    capped = normalized.loc[dates.notna() & (dates <= cutoff)].copy()
    if capped.empty:
        return {
            "status": "INVALID",
            "reason": "NO_TECHNICAL_DATA_ON_OR_BEFORE_ANALYSIS_DATE",
            "symbol": symbol,
            "analysis_cutoff": cutoff.date().isoformat(),
        }

    # Reuse the existing technical implementation unchanged.  Only its input
    # dataset is capped, so all indicators/milestones are causal as-of the
    # requested analysis session and cannot see later candles.
    with tempfile.TemporaryDirectory(prefix="sde_pm_asof_") as temp_dir:
        temp_root = Path(temp_dir)
        capped.to_csv(temp_root / f"{symbol}.csv", index=False)
        result = _ORIGINAL_TECHNICAL_SNAPSHOT(temp_root, symbol, buy_date)

    result = dict(result)
    result["path"] = str(path)
    result["analysis_cutoff"] = cutoff.date().isoformat()
    return result


def analyze_position(
    conn,
    position,
    *,
    analysis_date: str,
    historical_dir: Path,
    broker_path: Path,
    sector_metadata_path: Path,
    sector_rotation_path: Path,
    market: str,
) -> dict[str, Any]:
    """Delegate unchanged runtime logic with an analysis-date technical guard."""
    previous_snapshot = base.technical_snapshot
    base.technical_snapshot = lambda root, symbol, buy_date: technical_snapshot_as_of(
        root,
        symbol,
        buy_date,
        analysis_date,
    )
    try:
        return _ORIGINAL_ANALYZE_POSITION(
            conn,
            position,
            analysis_date=analysis_date,
            historical_dir=historical_dir,
            broker_path=broker_path,
            sector_metadata_path=sector_metadata_path,
            sector_rotation_path=sector_rotation_path,
            market=market,
        )
    finally:
        base.technical_snapshot = previous_snapshot


runtime.analyze_position = analyze_position


def main() -> int:
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
