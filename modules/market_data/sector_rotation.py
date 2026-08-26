from __future__ import annotations

"""Compatibility entry point for the shared sector analytics engine.

The calculation itself lives in :mod:`modules.market_data.sector_analytics` so
Market Outlook and Post Market do not maintain competing sector formulas.
"""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd

from modules.market_data.sector_analytics import DEFAULT_IHSG_PATH, build_multiday_sector_rotation
from swing_utils import write_json


_TECHNICAL_DATE_ALIASES = (
    "Date",
    "Latest_Valid_Candle_Date",
    "Latest Valid Candle Date",
    "Technical_Data_Date",
    "Technical Data Date",
    "trade_date",
)


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _find_column(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    mapping = {_norm(column): str(column) for column in frame.columns}
    for alias in aliases:
        found = mapping.get(_norm(alias))
        if found is not None:
            return found
    return None


def _latest_technical_date(technical_path: Path, trade_date: date) -> date | None:
    if not technical_path.exists() or technical_path.stat().st_size <= 0:
        return None
    try:
        frame = pd.read_csv(technical_path, low_memory=False)
    except Exception:
        return None
    if frame.empty:
        return None
    date_col = _find_column(frame, _TECHNICAL_DATE_ALIASES)
    if not date_col:
        return None
    parsed = pd.to_datetime(frame[date_col], errors="coerce")
    eligible = parsed.notna() & (parsed.dt.date <= trade_date)
    if not eligible.any():
        return None
    return parsed.loc[eligible].max().date()


def _aligned_ihsg_copy(ihsg_path: Path, cutoff_date: date, temp_root: Path) -> tuple[Path, bool]:
    """Return an IHSG file capped to the technical snapshot date when needed.

    Market Outlook can be rerun while the current IDX session is already open.
    Yahoo may then expose an in-progress daily IHSG candle even though the
    technical snapshot still represents the previous completed session. Sector
    relative returns must compare like-for-like completed sessions, so future
    benchmark rows are ignored for this calculation only. The canonical IHSG
    file is never modified.
    """
    if not ihsg_path.exists() or ihsg_path.stat().st_size <= 0:
        return ihsg_path, False
    try:
        frame = pd.read_csv(ihsg_path, low_memory=False)
    except Exception:
        return ihsg_path, False
    if frame.empty:
        return ihsg_path, False
    date_col = _find_column(frame, ("Date", "Datetime", "Timestamp"))
    if not date_col:
        return ihsg_path, False
    parsed = pd.to_datetime(frame[date_col], errors="coerce")
    future_rows = parsed.notna() & (parsed.dt.date > cutoff_date)
    if not future_rows.any():
        return ihsg_path, False
    aligned = frame.loc[parsed.notna() & (parsed.dt.date <= cutoff_date)].copy()
    aligned_path = temp_root / "IHSG_ALIGNED.csv"
    aligned.to_csv(aligned_path, index=False)
    return aligned_path, True


def produce_sector_rotation(
    technical_path: Path | str,
    output_path: Path | str,
    trade_date: date,
    *,
    metadata_path: Path | str | None = None,
    ihsg_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build the canonical Market Outlook multi-day sector rotation artifact.

    ``metadata_path`` is a local symbol->sector master. No live metadata API is
    called here. ``ihsg_path`` is optional for backward-compatible callers and
    defaults to ``data/input/IHSG.csv`` inside the shared engine.

    The IHSG benchmark is aligned to the latest technical snapshot date before
    relative 5D/20D returns are calculated. This prevents an in-progress
    current-session IHSG candle from invalidating a previous-session technical
    snapshot during an intraday Market Outlook rerun.
    """
    technical = Path(technical_path)
    output = Path(output_path)
    benchmark = Path(ihsg_path) if ihsg_path else DEFAULT_IHSG_PATH
    technical_date = _latest_technical_date(technical, trade_date)

    if technical_date is None:
        return build_multiday_sector_rotation(
            technical_path=technical,
            output_path=output,
            trade_date=trade_date,
            metadata_path=metadata_path,
            ihsg_path=benchmark,
        )

    with TemporaryDirectory(prefix="sde-sector-benchmark-") as temp_dir:
        aligned_benchmark, aligned = _aligned_ihsg_copy(
            benchmark,
            technical_date,
            Path(temp_dir),
        )
        payload = build_multiday_sector_rotation(
            technical_path=technical,
            output_path=output,
            trade_date=trade_date,
            metadata_path=metadata_path,
            ihsg_path=aligned_benchmark,
        )
        if aligned:
            payload["ihsg_path"] = str(benchmark)
            payload["ihsg_alignment_cutoff"] = technical_date.isoformat()
            write_json(output, payload)
        return payload


__all__ = ["produce_sector_rotation"]
