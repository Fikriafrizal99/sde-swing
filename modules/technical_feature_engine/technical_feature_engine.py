#!/usr/bin/env python3
"""Stockbit Technical Feature Engine V1.2.

The indicator formulas are preserved. V1.2 adds validated lineage and contract safety,
closed-candle freshness metadata, and per-run manifests so downstream candidate
selection can prove which valid candle it used.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import (  # noqa: E402
    dataframe_hash,
    ensure_dir,
    file_sha256,
    iso_now,
    make_run_id,
    write_json,
)

REQUIRED = {"Date", "Open", "High", "Low", "Close", "Volume"}


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.replace(0, np.nan)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = safe_div(avg_gain, avg_loss)
    out = 100 - (100 / (1 + rs))
    return out.where(avg_loss != 0, 100.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rolling_slope(series: pd.Series, period: int) -> pd.Series:
    x = np.arange(period, dtype=float)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    def calc(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        y_mean = values.mean()
        return float(((x - x_mean) * (values - y_mean)).sum() / denom)

    return series.rolling(period).apply(calc, raw=True)


def compute_features(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"])
    df = df.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
    if len(df) < 30:
        raise ValueError(f"Data terlalu pendek ({len(df)} baris; minimum 30)")

    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    df["Symbol"] = symbol

    # Returns and gaps
    df["Return_1D"] = c.pct_change(1) * 100
    df["Return_5D"] = c.pct_change(5) * 100
    df["Return_20D"] = c.pct_change(20) * 100
    df["Return_60D"] = c.pct_change(60) * 100
    df["Gap_Pct"] = (df["Open"] / c.shift(1) - 1) * 100
    df["Intraday_Return_Pct"] = (c / df["Open"] - 1) * 100
    candle_range = (h - l).replace(0, np.nan)
    df["Candle_Body_Pct_Range"] = ((c - df["Open"]).abs() / candle_range) * 100
    df["Candle_Upper_Wick_Pct_Range"] = ((h - pd.concat([df["Open"], c], axis=1).max(axis=1)) / candle_range) * 100
    df["Candle_Lower_Wick_Pct_Range"] = ((pd.concat([df["Open"], c], axis=1).min(axis=1) - l) / candle_range) * 100
    df["Close_Location_Value"] = ((c - l) / candle_range).clip(0, 1)
    df["Bullish_Candle"] = (c > df["Open"]).astype("int8")

    # Moving averages
    for p in [5, 10, 20, 50, 100, 200]:
        df[f"SMA_{p}"] = c.rolling(p).mean()
        df[f"Distance_SMA_{p}_Pct"] = (c / df[f"SMA_{p}"] - 1) * 100
    for p in [9, 12, 20, 26, 50, 200]:
        df[f"EMA_{p}"] = c.ewm(span=p, adjust=False, min_periods=p).mean()
        df[f"Distance_EMA_{p}_Pct"] = (c / df[f"EMA_{p}"] - 1) * 100

    # RSI, MACD, ATR
    df["RSI_14"] = rsi(c, 14)
    df["MACD"] = df["EMA_12"] - df["EMA_26"]
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False, min_periods=9).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]
    df["ATR_14"] = atr(df, 14)
    df["ATR_14_Pct"] = safe_div(df["ATR_14"], c) * 100

    # Bollinger Bands
    df["BB_Mid_20"] = c.rolling(20).mean()
    bb_std = c.rolling(20).std(ddof=0)
    df["BB_Upper_20"] = df["BB_Mid_20"] + 2 * bb_std
    df["BB_Lower_20"] = df["BB_Mid_20"] - 2 * bb_std
    df["BB_Width_Pct"] = safe_div(df["BB_Upper_20"] - df["BB_Lower_20"], df["BB_Mid_20"]) * 100
    df["BB_Position"] = safe_div(c - df["BB_Lower_20"], df["BB_Upper_20"] - df["BB_Lower_20"])

    # Volume and liquidity
    for p in [5, 20, 60]:
        df[f"Volume_MA_{p}"] = v.rolling(p).mean()
    df["Volume_Ratio_20"] = safe_div(v, df["Volume_MA_20"])
    df["Turnover_Value"] = c * v
    df["Turnover_MA_20"] = df["Turnover_Value"].rolling(20).mean()

    # Range, highs/lows, drawdown
    df["Range_Pct"] = safe_div(h - l, c.shift(1)) * 100
    for p in [20, 52, 252]:
        df[f"High_{p}"] = h.rolling(p).max()
        df[f"Low_{p}"] = l.rolling(p).min()
        df[f"Distance_High_{p}_Pct"] = (c / df[f"High_{p}"] - 1) * 100
        df[f"Distance_Low_{p}_Pct"] = (c / df[f"Low_{p}"] - 1) * 100
    rolling_peak = c.rolling(252, min_periods=20).max()
    df["Drawdown_252_Pct"] = (c / rolling_peak - 1) * 100

    # Volatility and trend
    daily_ret = c.pct_change()
    df["Volatility_20_Annualized_Pct"] = daily_ret.rolling(20).std() * math.sqrt(252) * 100
    df["Volatility_60_Annualized_Pct"] = daily_ret.rolling(60).std() * math.sqrt(252) * 100
    df["Slope_Close_20"] = rolling_slope(c, 20)
    df["Slope_SMA20_10"] = rolling_slope(df["SMA_20"], 10)

    # Boolean regimes stored as 0/1 for easier scoring
    df["Above_SMA20"] = (c > df["SMA_20"]).astype("int8")
    df["Above_SMA50"] = (c > df["SMA_50"]).astype("int8")
    df["Above_SMA200"] = (c > df["SMA_200"]).astype("int8")
    df["SMA20_Above_SMA50"] = (df["SMA_20"] > df["SMA_50"]).astype("int8")
    df["SMA50_Above_SMA200"] = (df["SMA_50"] > df["SMA_200"]).astype("int8")
    df["MACD_Bullish"] = (df["MACD"] > df["MACD_Signal"]).astype("int8")
    df["Volume_Above_MA20"] = (v > df["Volume_MA_20"]).astype("int8")
    previous_high_20 = df["High_20"].shift(1)
    df["Breakout_20D"] = (c >= previous_high_20).astype("int8")
    df["Distance_Breakout_20_Pct"] = (c / previous_high_20 - 1) * 100

    # Lightweight technical regime, not the final Decision Engine score
    bull = (
        df["Above_SMA20"]
        + df["Above_SMA50"]
        + df["SMA20_Above_SMA50"]
        + df["MACD_Bullish"]
    )
    bear = (
        (1 - df["Above_SMA20"])
        + (1 - df["Above_SMA50"])
        + (1 - df["MACD_Bullish"])
    )
    df["Technical_Regime"] = np.select(
        [bull >= 4, bear >= 3], ["bullish", "bearish"], default="neutral"
    )

    return df


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    aliases = {
        "date": "Date", "datetime": "Date", "timestamp": "Date",
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "adj close": "Adj Close", "adj_close": "Adj Close", "volume": "Volume",
    }
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in aliases:
            rename[col] = aliases[key]
    return df.rename(columns=rename)


def validate_ohlcv(raw: pd.DataFrame, symbol: str, allow_partial: bool) -> tuple[pd.DataFrame, dict]:
    if raw is None or raw.empty:
        raise ValueError("Input kosong")
    missing = REQUIRED - set(raw.columns)
    if missing:
        raise ValueError("Kolom wajib tidak ada: " + ", ".join(sorted(missing)))

    work = raw.copy()
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    dup_count = int(work.duplicated(subset=["Date"]).sum())
    latest_input = work["Date"].max()
    valid = work.dropna(subset=list(REQUIRED)).sort_values("Date").drop_duplicates("Date", keep="last")
    if valid.empty:
        raise ValueError("Tidak ada closed candle valid; Close/Open/High/Low/Volume wajib terisi")
    latest_valid = valid["Date"].max()
    partial_latest = pd.notna(latest_input) and pd.notna(latest_valid) and latest_input > latest_valid
    if partial_latest and not allow_partial:
        work = work[work["Date"] <= latest_valid].copy()
    meta = {
        "symbol": symbol,
        "input_rows": int(len(raw)),
        "valid_rows": int(len(valid)),
        "duplicate_symbol_date": dup_count,
        "latest_input_date": latest_input.date().isoformat() if pd.notna(latest_input) else "",
        "latest_valid_candle_date": latest_valid.date().isoformat() if pd.notna(latest_valid) else "",
        "partial_latest_ignored": bool(partial_latest and not allow_partial),
    }
    return work, meta


def input_hash(input_dir: Path) -> str:
    hashes = []
    for file in sorted(input_dir.glob("*.csv")):
        hashes.append(f"{file.name}:{file_sha256(file)}")
    digest = dataframe_hash(pd.DataFrame({"hash": hashes}), short=False)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Hitung technical features dari OHLCV Stockbit Downloader")
    parser.add_argument("--input", default="historical_output/by_symbol", help="Folder CSV OHLCV per simbol")
    parser.add_argument("--output", default="technical_output", help="Folder keluaran")
    parser.add_argument("--timeseries", action="store_true", help="Simpan seluruh feature timeseries gabungan")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest-dir", default=None)
    parser.add_argument("--data-quality-status", default="VALID")
    parser.add_argument("--allow-partial-daily-candle", action="store_true")
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    logs_dir = output_dir / "logs"
    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else output_dir.parent / "manifests"
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_dir(manifest_dir)
    setup_logging(logs_dir / "technical_feature_engine.log")

    files = sorted(input_dir.glob("*.csv"))
    if not input_dir.exists():
        logging.error("Input folder tidak ditemukan: %s", input_dir.resolve())
        return 1
    if not files:
        logging.error("Tidak ada CSV di %s", input_dir.resolve())
        return 1

    latest_rows: list[pd.DataFrame] = []
    all_frames: list[pd.DataFrame] = []
    status_rows: list[dict] = []
    generated_at = iso_now()

    logging.info("Memproses %d file OHLCV", len(files))
    for i, path in enumerate(files, 1):
        symbol = path.stem.upper().replace(".JK", "")
        try:
            raw = normalize_columns(pd.read_csv(path))
            validated, meta = validate_ohlcv(raw, symbol, args.allow_partial_daily_candle)
            feat = compute_features(validated, symbol)
            latest = feat.tail(1).copy()
            latest["Run_ID"] = args.run_id
            latest["Source_Historical_Path"] = str(path.resolve())
            latest["Source_Historical_Hash"] = file_sha256(path, short=True)
            latest["Latest_Valid_Candle_Date"] = meta["latest_valid_candle_date"]
            latest["Technical_Generated_At"] = generated_at
            latest["Technical_Row_Count"] = len(feat)
            latest["Data_Quality_Status"] = args.data_quality_status
            latest_rows.append(latest)
            if args.timeseries:
                tagged = feat.copy()
                tagged["Run_ID"] = args.run_id
                tagged["Source_Historical_Path"] = str(path.resolve())
                tagged["Latest_Valid_Candle_Date"] = meta["latest_valid_candle_date"]
                tagged["Technical_Generated_At"] = generated_at
                tagged["Data_Quality_Status"] = args.data_quality_status
                all_frames.append(tagged)
            status_rows.append({
                "symbol": symbol, "status": "success", "rows": len(feat),
                "first_date": feat["Date"].min().date().isoformat(),
                "last_date": feat["Date"].max().date().isoformat(), "message": "",
                **meta,
            })
            if i % 25 == 0 or i == len(files):
                logging.info("Progress %d/%d", i, len(files))
        except Exception as exc:
            logging.warning("%s gagal: %s", symbol, exc)
            status_rows.append({
                "symbol": symbol, "status": "failed", "rows": 0,
                "first_date": "", "last_date": "", "message": str(exc),
                "input_rows": 0, "valid_rows": 0, "duplicate_symbol_date": 0,
                "latest_input_date": "", "latest_valid_candle_date": "",
                "partial_latest_ignored": False,
            })

    status = pd.DataFrame(status_rows)
    status.to_csv(logs_dir / "feature_status.csv", index=False, encoding="utf-8-sig")
    failed = status[status["status"] == "failed"]
    failed.to_csv(logs_dir / "failed_symbols.csv", index=False, encoding="utf-8-sig")

    if not latest_rows:
        logging.error("Tidak ada simbol yang berhasil diproses")
        return 2

    latest_df = pd.concat(latest_rows, ignore_index=True)
    latest_df = latest_df.sort_values("Symbol").reset_index(drop=True)
    latest_df["Date"] = pd.to_datetime(latest_df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    latest_path = output_dir / "latest_technical_features.csv"
    latest_df.to_csv(latest_path, index=False, encoding="utf-8-sig", float_format="%.6f")

    timeseries_path = ""
    if args.timeseries and all_frames:
        ts = pd.concat(all_frames, ignore_index=True)
        ts["Date"] = pd.to_datetime(ts["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
        timeseries_path = str((output_dir / "technical_features_timeseries.csv").resolve())
        ts.to_csv(output_dir / "technical_features_timeseries.csv", index=False, encoding="utf-8-sig", float_format="%.6f")

    latest_valid_dates = pd.to_datetime(status["latest_valid_candle_date"], errors="coerce")
    valid_latest_dates = latest_valid_dates.dropna()
    latest_valid = valid_latest_dates.max().date().isoformat() if not valid_latest_dates.empty else ""
    source_hash = input_hash(input_dir)
    manifest = {
        "Run_ID": args.run_id,
        "generated_at": generated_at,
        "input_folder": str(input_dir.resolve()),
        "Source_Historical_Path": str(input_dir.resolve()),
        "Source_Historical_Hash": source_hash,
        "Latest_Valid_Candle_Date": latest_valid,
        "Technical_Generated_At": generated_at,
        "Technical_Row_Count": int(len(latest_df)),
        "Technical_Symbol_Count": int(latest_df["Symbol"].nunique()),
        "symbols_success": int((status["status"] == "success").sum()),
        "symbols_failed": int((status["status"] == "failed").sum()),
        "partial_latest_ignored": int(status["partial_latest_ignored"].fillna(False).astype(bool).sum()),
        "duplicate_symbol_date": int(status["duplicate_symbol_date"].fillna(0).astype(int).sum()),
        "latest_feature_rows": int(len(latest_df)),
        "latest_feature_columns": int(len(latest_df.columns)),
        "latest_output": str(latest_path.resolve()),
        "latest_output_hash": file_sha256(latest_path),
        "timeseries_saved": bool(args.timeseries),
        "timeseries_output": timeseries_path,
        "Data_Quality_Status": args.data_quality_status,
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(manifest_dir / f"TECHNICAL_MANIFEST_{args.run_id}.json", manifest)
    logging.info("Selesai. Success=%d, failed=%d", manifest["symbols_success"], manifest["symbols_failed"])
    logging.info("Output utama: %s", latest_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
