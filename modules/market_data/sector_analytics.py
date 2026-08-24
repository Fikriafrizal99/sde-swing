from __future__ import annotations

"""Shared presentation-only sector analytics for Market Outlook and Post Market.

This module owns the only sector aggregation logic used by the two reports:

* ``build_daily_sector_pulse``: current-session sector strength for Post Market.
* ``build_multiday_sector_rotation``: 5D/20D relative rotation versus IHSG for
  Market Outlook.

It reads a local symbol->sector mapping plus already-produced OHLCV technical
features. It never changes candidate scores, decisions, broker facts, entry
levels, lifecycle state, or any other SDE engine output.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from swing_utils import PACKAGE_VERSION, write_json


DEFAULT_SECTOR_MASTER = Path("data/input/sector_metadata.csv")
DEFAULT_IHSG_PATH = Path("data/input/IHSG.csv")
DEFAULT_MIN_COVERAGE = 0.90
DEFAULT_MIN_SECTOR_SYMBOLS = 3


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {_norm(column): str(column) for column in frame.columns}
    for alias in aliases:
        found = mapping.get(_norm(alias))
        if found is not None:
            return found
    return None


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _empty_payload(
    trade_date: date,
    output_path: Path,
    *,
    mode: str,
    metadata_path: Path,
    data_date: str = "",
    reason: str = "INSUFFICIENT_DATA",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": PACKAGE_VERSION,
        "trade_date": trade_date.isoformat(),
        "data_date": data_date,
        "generated_at": pd.Timestamp.now(tz="Asia/Jakarta").isoformat(timespec="seconds"),
        "provider": "LOCAL_OHLCV",
        "source_mode": mode,
        "status": "INSUFFICIENT_DATA",
        "reason": reason,
        "coverage": 0.0,
        "metadata_path": str(metadata_path),
        "sectors": [],
        "output_path": str(output_path),
    }
    if mode == "MULTIDAY_RELATIVE_ROTATION":
        payload.update({
            "leading": [],
            "improving": [],
            "weakening": [],
            "lagging": [],
        })
    return payload


def load_sector_mapping(path: Path | str | None = None) -> pd.DataFrame:
    """Load the local authoritative symbol->sector mapping.

    Extra metadata columns are ignored deliberately. Sector analytics only
    consumes ``Symbol`` and ``Sector`` and therefore has no live metadata API
    dependency.
    """
    source = Path(path) if path else DEFAULT_SECTOR_MASTER
    frame = _read_csv(source)
    if frame.empty:
        return pd.DataFrame(columns=["Symbol", "Sector"])
    symbol_col = _column(frame, "Symbol", "Ticker", "EMITEN", "Code")
    sector_col = _column(frame, "Sector", "Sektor", "Sector_Name")
    if not symbol_col or not sector_col:
        return pd.DataFrame(columns=["Symbol", "Sector"])
    result = frame[[symbol_col, sector_col]].copy()
    result.columns = ["Symbol", "Sector"]
    result["Symbol"] = result["Symbol"].astype(str).str.strip().str.upper().str.replace(".JK", "", regex=False)
    result["Sector"] = result["Sector"].astype(str).str.strip()
    result = result.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA}).dropna()
    return result.drop_duplicates("Symbol", keep="last").reset_index(drop=True)


def _latest_technical(frame: pd.DataFrame, trade_date: date) -> tuple[pd.DataFrame, str]:
    if frame.empty:
        return frame, ""
    date_col = _column(
        frame,
        "Date",
        "Latest_Valid_Candle_Date",
        "Latest Valid Candle Date",
        "Technical_Data_Date",
        "Technical Data Date",
        "trade_date",
    )
    if not date_col:
        return frame.copy(), ""
    parsed = pd.to_datetime(frame[date_col], errors="coerce")
    eligible = parsed.notna() & (parsed.dt.date <= trade_date)
    if not eligible.any():
        return pd.DataFrame(), ""
    latest = parsed.loc[eligible].max().date()
    return frame.loc[parsed.dt.date == latest].copy(), latest.isoformat()


def _joined_snapshot(
    technical_path: Path,
    metadata_path: Path,
    trade_date: date,
) -> tuple[pd.DataFrame, str, float]:
    technical, data_date = _latest_technical(_read_csv(technical_path), trade_date)
    mapping = load_sector_mapping(metadata_path)
    if technical.empty or mapping.empty:
        return pd.DataFrame(), data_date, 0.0
    symbol_col = _column(technical, "Symbol", "Ticker", "EMITEN")
    if not symbol_col:
        return pd.DataFrame(), data_date, 0.0
    work = technical.copy()
    work["__symbol"] = work[symbol_col].astype(str).str.strip().str.upper().str.replace(".JK", "", regex=False)
    total_symbols = work["__symbol"].replace("", pd.NA).dropna().nunique()
    mapping = mapping.rename(columns={"Symbol": "__symbol", "Sector": "__sector"})
    work = work.merge(mapping, on="__symbol", how="left")
    mapped = work["__sector"].notna() & work["__sector"].astype(str).str.strip().ne("")
    mapped_symbols = work.loc[mapped, "__symbol"].nunique()
    coverage = (mapped_symbols / total_symbols) if total_symbols else 0.0
    return work.loc[mapped].copy(), data_date, float(coverage)


def _percent_rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True, method="average").fillna(0.0)


def build_daily_sector_pulse(
    technical_path: Path | str,
    output_path: Path | str,
    trade_date: date,
    *,
    metadata_path: Path | str | None = None,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_sector_symbols: int = DEFAULT_MIN_SECTOR_SYMBOLS,
) -> dict[str, Any]:
    """Build one current-session sector pulse from OHLCV-derived features.

    Ranking uses three independent facts from the same technical snapshot:
    median Return_1D (50%), positive breadth (30%), and positive-turnover
    participation (20%). Turnover participation is directional inside each
    sector, avoiding a structural bias toward sectors that are simply larger.
    """
    technical_path = Path(technical_path)
    output_path = Path(output_path)
    metadata = Path(metadata_path) if metadata_path else DEFAULT_SECTOR_MASTER
    joined, data_date, mapping_coverage = _joined_snapshot(technical_path, metadata, trade_date)
    if joined.empty or data_date != trade_date.isoformat():
        payload = _empty_payload(
            trade_date,
            output_path,
            mode="DAILY_SECTOR_PULSE",
            metadata_path=metadata,
            data_date=data_date,
            reason="TECHNICAL_SESSION_NOT_CURRENT" if data_date else "TECHNICAL_OR_SECTOR_MASTER_UNAVAILABLE",
        )
        write_json(output_path, payload)
        return payload

    return_col = _column(joined, "Return_1D", "Return1D", "1D_Return")
    turnover_col = _column(joined, "Turnover_Value", "Turnover Value", "Turnover")
    close_col = _column(joined, "Close", "Last_Price", "Current_Price")
    volume_col = _column(joined, "Volume")
    if not return_col:
        payload = _empty_payload(
            trade_date, output_path, mode="DAILY_SECTOR_PULSE", metadata_path=metadata,
            data_date=data_date, reason="RETURN_1D_MISSING",
        )
        payload["coverage"] = round(mapping_coverage, 4)
        write_json(output_path, payload)
        return payload

    work = joined.copy()
    work["__r1"] = pd.to_numeric(work[return_col], errors="coerce")
    if turnover_col:
        work["__turnover"] = pd.to_numeric(work[turnover_col], errors="coerce")
    elif close_col and volume_col:
        work["__turnover"] = (
            pd.to_numeric(work[close_col], errors="coerce")
            * pd.to_numeric(work[volume_col], errors="coerce")
        )
    else:
        work["__turnover"] = pd.NA
    total_mapped = work["__symbol"].nunique()
    work = work.dropna(subset=["__r1", "__turnover"])
    valid_coverage = (work["__symbol"].nunique() / total_mapped) if total_mapped else 0.0
    coverage = min(mapping_coverage, valid_coverage)

    rows: list[dict[str, Any]] = []
    for sector, group in work.groupby("__sector", dropna=True):
        symbol_count = int(group["__symbol"].nunique())
        if symbol_count < max(1, int(min_sector_symbols)):
            continue
        turnover = pd.to_numeric(group["__turnover"], errors="coerce").clip(lower=0).fillna(0.0)
        total_turnover = float(turnover.sum())
        positive = group["__r1"] > 0
        positive_turnover = float(turnover.loc[positive].sum())
        rows.append({
            "sector": str(sector),
            "symbol_count": symbol_count,
            "median_return_1d": float(group["__r1"].median()),
            "mean_return_1d": float(group["__r1"].mean()),
            "positive_breadth": float(positive.mean()),
            "positive_turnover_ratio": (positive_turnover / total_turnover) if total_turnover > 0 else 0.0,
            "turnover": total_turnover,
        })

    grouped = pd.DataFrame(rows)
    if grouped.empty or coverage < float(min_coverage):
        payload = _empty_payload(
            trade_date, output_path, mode="DAILY_SECTOR_PULSE", metadata_path=metadata,
            data_date=data_date, reason="COVERAGE_BELOW_MINIMUM" if coverage < float(min_coverage) else "NO_ELIGIBLE_SECTORS",
        )
        payload["coverage"] = round(float(coverage), 4)
        write_json(output_path, payload)
        return payload

    grouped["return_rank"] = _percent_rank(grouped["median_return_1d"])
    grouped["breadth_rank"] = _percent_rank(grouped["positive_breadth"])
    grouped["participation_rank"] = _percent_rank(grouped["positive_turnover_ratio"])
    grouped["daily_score"] = (
        0.50 * grouped["return_rank"]
        + 0.30 * grouped["breadth_rank"]
        + 0.20 * grouped["participation_rank"]
    )
    grouped = grouped.sort_values(
        ["daily_score", "median_return_1d", "positive_breadth"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    grouped["rank"] = range(1, len(grouped) + 1)

    sectors: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        sectors.append({
            "sector": row["sector"],
            "rank": int(row["rank"]),
            "symbol_count": int(row["symbol_count"]),
            "median_return_1d": round(float(row["median_return_1d"]), 4),
            "mean_return_1d": round(float(row["mean_return_1d"]), 4),
            "positive_breadth": round(float(row["positive_breadth"]), 4),
            "positive_turnover_ratio": round(float(row["positive_turnover_ratio"]), 4),
            "daily_score": round(float(row["daily_score"]), 6),
        })

    payload = {
        "schema_version": PACKAGE_VERSION,
        "trade_date": trade_date.isoformat(),
        "data_date": data_date,
        "generated_at": pd.Timestamp.now(tz="Asia/Jakarta").isoformat(timespec="seconds"),
        "provider": "LOCAL_OHLCV",
        "source_mode": "DAILY_SECTOR_PULSE",
        "status": "VALID",
        "coverage": round(float(coverage), 4),
        "minimum_coverage": float(min_coverage),
        "minimum_sector_symbols": int(min_sector_symbols),
        "metadata_path": str(metadata),
        "technical_path": str(technical_path),
        "formula": "score=0.50*rank(median Return_1D)+0.30*rank(positive breadth)+0.20*rank(positive-turnover participation)",
        "sectors": sectors,
        "output_path": str(output_path),
    }
    write_json(output_path, payload)
    return payload


def _benchmark_returns(ihsg_path: Path, trade_date: date, expected_data_date: str) -> tuple[float | None, float | None, str]:
    frame = _read_csv(ihsg_path)
    if frame.empty:
        return None, None, ""
    date_col = _column(frame, "Date", "Datetime", "Timestamp")
    close_col = _column(frame, "Close", "Adj Close", "Adj_Close")
    if not date_col or not close_col:
        return None, None, ""
    parsed = pd.to_datetime(frame[date_col], errors="coerce")
    work = frame.loc[parsed.notna() & (parsed.dt.date <= trade_date), [date_col, close_col]].copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work[close_col] = pd.to_numeric(work[close_col], errors="coerce")
    work = work.dropna().sort_values(date_col).drop_duplicates(date_col, keep="last")
    if len(work) < 21:
        return None, None, ""
    data_date = work[date_col].iloc[-1].date().isoformat()
    if expected_data_date and data_date != expected_data_date:
        return None, None, data_date
    close = work[close_col]
    r5 = float(close.pct_change(5).iloc[-1] * 100.0)
    r20 = float(close.pct_change(20).iloc[-1] * 100.0)
    return r5, r20, data_date


def build_multiday_sector_rotation(
    technical_path: Path | str,
    output_path: Path | str,
    trade_date: date,
    *,
    metadata_path: Path | str | None = None,
    ihsg_path: Path | str | None = None,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_sector_symbols: int = DEFAULT_MIN_SECTOR_SYMBOLS,
) -> dict[str, Any]:
    """Build 5D/20D sector rotation relative to IHSG from one shared snapshot."""
    technical_path = Path(technical_path)
    output_path = Path(output_path)
    metadata = Path(metadata_path) if metadata_path else DEFAULT_SECTOR_MASTER
    benchmark_path = Path(ihsg_path) if ihsg_path else DEFAULT_IHSG_PATH
    joined, data_date, mapping_coverage = _joined_snapshot(technical_path, metadata, trade_date)
    if joined.empty:
        payload = _empty_payload(
            trade_date, output_path, mode="MULTIDAY_RELATIVE_ROTATION", metadata_path=metadata,
            data_date=data_date, reason="TECHNICAL_OR_SECTOR_MASTER_UNAVAILABLE",
        )
        write_json(output_path, payload)
        return payload

    r5_col = _column(joined, "Return_5D", "Return5D", "5D_Return")
    r20_col = _column(joined, "Return_20D", "Return20D", "20D_Return")
    if not r5_col or not r20_col:
        payload = _empty_payload(
            trade_date, output_path, mode="MULTIDAY_RELATIVE_ROTATION", metadata_path=metadata,
            data_date=data_date, reason="RETURN_5D_OR_20D_MISSING",
        )
        payload["coverage"] = round(mapping_coverage, 4)
        write_json(output_path, payload)
        return payload

    work = joined.copy()
    work["__r5"] = pd.to_numeric(work[r5_col], errors="coerce")
    work["__r20"] = pd.to_numeric(work[r20_col], errors="coerce")
    total_mapped = work["__symbol"].nunique()
    work = work.dropna(subset=["__r5", "__r20"])
    valid_coverage = (work["__symbol"].nunique() / total_mapped) if total_mapped else 0.0
    coverage = min(mapping_coverage, valid_coverage)

    ihsg_r5, ihsg_r20, benchmark_date = _benchmark_returns(benchmark_path, trade_date, data_date)
    if ihsg_r5 is None or ihsg_r20 is None:
        payload = _empty_payload(
            trade_date, output_path, mode="MULTIDAY_RELATIVE_ROTATION", metadata_path=metadata,
            data_date=data_date, reason="IHSG_BENCHMARK_NOT_ALIGNED",
        )
        payload.update({
            "coverage": round(float(coverage), 4),
            "ihsg_path": str(benchmark_path),
            "ihsg_data_date": benchmark_date,
        })
        write_json(output_path, payload)
        return payload

    rows: list[dict[str, Any]] = []
    for sector, group in work.groupby("__sector", dropna=True):
        symbol_count = int(group["__symbol"].nunique())
        if symbol_count < max(1, int(min_sector_symbols)):
            continue
        sector_r5 = float(group["__r5"].median())
        sector_r20 = float(group["__r20"].median())
        rs5 = sector_r5 - ihsg_r5
        rs20 = sector_r20 - ihsg_r20
        rows.append({
            "sector": str(sector),
            "symbol_count": symbol_count,
            "return_5d": sector_r5,
            "return_20d": sector_r20,
            "rs_5d": rs5,
            "rs_20d": rs20,
            "relative_strength": 0.60 * rs20 + 0.40 * rs5,
            "relative_momentum": rs5 - rs20 / 4.0,
        })

    grouped = pd.DataFrame(rows)
    if grouped.empty or coverage < float(min_coverage):
        payload = _empty_payload(
            trade_date, output_path, mode="MULTIDAY_RELATIVE_ROTATION", metadata_path=metadata,
            data_date=data_date, reason="COVERAGE_BELOW_MINIMUM" if coverage < float(min_coverage) else "NO_ELIGIBLE_SECTORS",
        )
        payload.update({
            "coverage": round(float(coverage), 4),
            "ihsg_return_5d": round(ihsg_r5, 4),
            "ihsg_return_20d": round(ihsg_r20, 4),
            "ihsg_data_date": benchmark_date,
        })
        write_json(output_path, payload)
        return payload

    grouped["strength_rank"] = _percent_rank(grouped["relative_strength"])
    grouped["momentum_rank"] = _percent_rank(grouped["relative_momentum"])
    grouped["quadrant"] = "LAGGING"
    grouped.loc[(grouped["strength_rank"] >= 0.5) & (grouped["momentum_rank"] >= 0.5), "quadrant"] = "LEADING"
    grouped.loc[(grouped["strength_rank"] < 0.5) & (grouped["momentum_rank"] >= 0.5), "quadrant"] = "IMPROVING"
    grouped.loc[(grouped["strength_rank"] >= 0.5) & (grouped["momentum_rank"] < 0.5), "quadrant"] = "WEAKENING"
    grouped = grouped.sort_values(["strength_rank", "momentum_rank"], ascending=False).reset_index(drop=True)

    sectors: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        sectors.append({
            "sector": row["sector"],
            "symbol_count": int(row["symbol_count"]),
            "return_5d": round(float(row["return_5d"]), 4),
            "return_20d": round(float(row["return_20d"]), 4),
            "rs_5d": round(float(row["rs_5d"]), 4),
            "rs_20d": round(float(row["rs_20d"]), 4),
            "relative_strength": round(float(row["relative_strength"]), 4),
            "relative_momentum": round(float(row["relative_momentum"]), 4),
            "strength_rank": round(float(row["strength_rank"]), 6),
            "momentum_rank": round(float(row["momentum_rank"]), 6),
            "quadrant": str(row["quadrant"]),
        })

    buckets = {
        key.lower(): [row["sector"] for row in sectors if row["quadrant"] == key]
        for key in ("LEADING", "IMPROVING", "WEAKENING", "LAGGING")
    }
    payload = {
        "schema_version": PACKAGE_VERSION,
        "trade_date": trade_date.isoformat(),
        "data_date": data_date,
        "generated_at": pd.Timestamp.now(tz="Asia/Jakarta").isoformat(timespec="seconds"),
        "provider": "LOCAL_OHLCV",
        "source_mode": "MULTIDAY_RELATIVE_ROTATION",
        "status": "VALID",
        "coverage": round(float(coverage), 4),
        "minimum_coverage": float(min_coverage),
        "minimum_sector_symbols": int(min_sector_symbols),
        "metadata_path": str(metadata),
        "technical_path": str(technical_path),
        "ihsg_path": str(benchmark_path),
        "ihsg_data_date": benchmark_date,
        "ihsg_return_5d": round(ihsg_r5, 4),
        "ihsg_return_20d": round(ihsg_r20, 4),
        "formula": "RS_5D=sector_median_5D-IHSG_5D; RS_20D=sector_median_20D-IHSG_20D; strength=0.60*RS_20D+0.40*RS_5D; momentum=RS_5D-RS_20D/4; quadrant=50th percentile split of strength/momentum ranks",
        "sectors": sectors,
        **buckets,
        "output_path": str(output_path),
    }
    write_json(output_path, payload)
    return payload


__all__ = [
    "DEFAULT_IHSG_PATH",
    "DEFAULT_MIN_COVERAGE",
    "DEFAULT_MIN_SECTOR_SYMBOLS",
    "DEFAULT_SECTOR_MASTER",
    "build_daily_sector_pulse",
    "build_multiday_sector_rotation",
    "load_sector_mapping",
]
