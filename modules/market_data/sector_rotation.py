from __future__ import annotations

"""Presentation-only sector rotation producer.

The producer deliberately does not feed candidate, decision, or score stages.
It consumes the technical snapshot plus optional symbol-sector metadata and
writes one canonical artifact for Market Outlook.  Missing sector metadata is
reported as ``INSUFFICIENT_DATA`` rather than replaced with an inferred sector.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from swing_utils import write_json


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {_norm(column): column for column in frame.columns}
    for alias in aliases:
        if _norm(alias) in mapping:
            return mapping[_norm(alias)]
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _empty(trade_date: date, output_path: Path, source_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "1.7.0-multisource",
        "trade_date": trade_date.isoformat(),
        "generated_at": pd.Timestamp.now(tz="Asia/Jakarta").isoformat(timespec="seconds"),
        "provider": "NONE",
        "source_mode": "UNAVAILABLE",
        "status": "INSUFFICIENT_DATA",
        "coverage": 0.0,
        "source_path": str(source_path),
        "formula": "sector metadata + technical Return_5D/Return_20D; no sector fallback",
        "leading": [],
        "improving": [],
        "weakening": [],
        "lagging": [],
        "output_path": str(output_path),
    }


def produce_sector_rotation(
    technical_path: Path | str,
    output_path: Path | str,
    trade_date: date,
    *,
    metadata_path: Path | str | None = None,
) -> dict[str, Any]:
    """Write the canonical sector rotation artifact and return its payload.

    A sector's strength is ``0.6 * mean(Return_20D) + 0.4 * mean(Return_5D)``.
    Momentum is ``mean(Return_5D) - mean(Return_20D) / 4``.  Buckets are
    presentation labels based on cross-sector ranks; they never alter stock
    scoring or decision thresholds.
    """

    technical_path = Path(technical_path)
    output_path = Path(output_path)
    metadata = Path(metadata_path) if metadata_path else None
    # Preserve the configured metadata path in the artifact even when it is
    # missing; this makes the fail-closed reason auditable.
    source_path = metadata if metadata else technical_path
    payload: dict[str, Any]
    try:
        technical = pd.read_csv(technical_path, low_memory=False)
    except Exception:
        technical = pd.DataFrame()
    if technical.empty:
        payload = _empty(trade_date, output_path, source_path)
    else:
        symbol_col = _column(technical, "Symbol", "Ticker", "EMITEN")
        date_col = _column(technical, "Date", "Technical_Data_Date", "Latest_Valid_Candle_Date")
        sector_col = _column(technical, "Sector", "Sektor", "Sector_Name", "SubSector", "Sub_Sector", "SubSektor", "Industry")
        if metadata and metadata.exists() and symbol_col:
            try:
                sector_frame = pd.read_csv(metadata, low_memory=False)
            except Exception:
                sector_frame = pd.DataFrame()
            metadata_symbol = _column(sector_frame, "Symbol", "Ticker", "EMITEN")
            metadata_sector = _column(sector_frame, "Sector", "Sektor", "Sector_Name", "SubSector", "Sub_Sector", "SubSektor", "Industry")
            if not sector_frame.empty and metadata_symbol and metadata_sector:
                lookup = sector_frame[[metadata_symbol, metadata_sector]].copy()
                lookup.columns = ["__symbol", "__sector"]
                lookup["__symbol"] = lookup["__symbol"].astype(str).str.upper().str.replace(".JK", "", regex=False)
                technical["__symbol"] = technical[symbol_col].astype(str).str.upper().str.replace(".JK", "", regex=False)
                technical = technical.merge(lookup.drop_duplicates("__symbol"), on="__symbol", how="left")
                sector_col = "__sector"
        if not symbol_col or not sector_col:
            payload = _empty(trade_date, output_path, source_path)
        else:
            if date_col:
                parsed = pd.to_datetime(technical[date_col], errors="coerce")
                eligible = parsed.notna() & (parsed.dt.date <= trade_date)
                technical = technical.loc[eligible].copy()
                if not technical.empty:
                    latest = pd.to_datetime(technical[date_col], errors="coerce").max()
                    technical = technical.loc[pd.to_datetime(technical[date_col], errors="coerce").dt.date == latest.date()].copy()
            total_symbols = technical[symbol_col].dropna().astype(str).nunique()
            technical["__sector_name"] = technical[sector_col].astype(str).str.strip()
            technical["__sector_name"] = technical["__sector_name"].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
            eligible = technical.dropna(subset=["__sector_name"]).copy()
            coverage = (eligible[symbol_col].astype(str).nunique() / total_symbols) if total_symbols else 0.0
            r5_col = _column(eligible, "Return_5D", "Return5D", "5D_Return")
            r20_col = _column(eligible, "Return_20D", "Return20D", "20D_Return")
            if eligible.empty or not r5_col or not r20_col:
                payload = _empty(trade_date, output_path, source_path)
                payload["coverage"] = round(float(coverage), 4)
            else:
                eligible["__r5"] = pd.to_numeric(eligible[r5_col], errors="coerce")
                eligible["__r20"] = pd.to_numeric(eligible[r20_col], errors="coerce")
                eligible = eligible.dropna(subset=["__r5", "__r20"])
                grouped = eligible.groupby("__sector_name", dropna=True).agg(
                    return_5d=("__r5", "mean"),
                    return_20d=("__r20", "mean"),
                    symbol_count=(symbol_col, "nunique"),
                ).reset_index().rename(columns={"__sector_name": "sector"})
                grouped["strength"] = 0.6 * grouped["return_20d"] + 0.4 * grouped["return_5d"]
                grouped["momentum"] = grouped["return_5d"] - grouped["return_20d"] / 4.0
                grouped["strength_rank"] = grouped["strength"].rank(pct=True, method="average")
                grouped["momentum_rank"] = grouped["momentum"].rank(pct=True, method="average")
                buckets = {"leading": [], "improving": [], "weakening": [], "lagging": []}
                for row in grouped.sort_values(["strength", "momentum"], ascending=False).itertuples(index=False):
                    if row.strength_rank >= 0.75 and row.momentum >= 0:
                        bucket = "leading"
                    elif row.momentum >= 0:
                        bucket = "improving"
                    elif row.strength_rank >= 0.25:
                        bucket = "weakening"
                    else:
                        bucket = "lagging"
                    # Buckets stay UI-friendly sector labels.  The formula and
                    # source path in the artifact provide the audit lineage;
                    # no decision field is embedded in this presentation file.
                    buckets[bucket].append(str(row.sector))
                payload = {
                    "schema_version": "1.7.0-multisource",
                    "trade_date": trade_date.isoformat(),
                    "generated_at": pd.Timestamp.now(tz="Asia/Jakarta").isoformat(timespec="seconds"),
                    "provider": "LOCAL_TECHNICAL",
                    "source_mode": "TECHNICAL_SECTOR_METADATA" if metadata and metadata.exists() else "TECHNICAL_SECTOR_COLUMNS",
                    "status": "VALID" if coverage > 0 and not grouped.empty else "INSUFFICIENT_DATA",
                    "coverage": round(float(coverage), 4),
                    "source_path": str(source_path),
                    "formula": "strength=0.6*mean(Return_20D)+0.4*mean(Return_5D); momentum=mean(Return_5D)-mean(Return_20D)/4",
                    **buckets,
                    "output_path": str(output_path),
                }
    write_json(output_path, payload)
    return payload
