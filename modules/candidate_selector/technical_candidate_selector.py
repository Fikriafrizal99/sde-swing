#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import dataframe_hash, file_sha256, make_run_id, write_json
from modules.runtime_config import load_runtime_config


EXCLUDED = {"IHSG", "BRENT", "OIL", "XAU", "JECX", "JELI"}
LINEAGE_COLUMNS = {
    "Run_ID",
    "Technical_Source_Path",
    "Technical_Source_Hash",
    "Technical_Data_Date",
    "Candidate_Generated_At",
    "Data_Quality_Status",
}

ALIASES = {
    "symbol": ["symbol", "emiten", "ticker", "code"],
    "close": ["close", "last", "price", "adj close", "adj_close"],
    "open": ["open"],
    "high": ["high"],
    "low": ["low"],
    "sma5": ["sma_5", "sma5", "ma_5", "ma5", "price_ma_5"],
    "sma20": ["sma_20", "sma20", "ma_20", "ma20"],
    "sma50": ["sma_50", "sma50", "ma_50", "ma50"],
    "sma200": ["sma_200", "sma200", "ma_200", "ma200"],
    "ema20": ["ema_20", "ema20"],
    "dist_ema20": ["distance_ema_20_pct", "distance_ema20_pct", "dist_ema20"],
    "rsi": ["rsi_14", "rsi14", "rsi"],
    "macd": ["macd", "macd_line"],
    "macd_signal": ["macd_signal", "signal", "macd_signal_line"],
    "macd_hist": ["macd_hist", "macd_histogram", "histogram"],
    "volume": ["volume"],
    "volume_ma20": ["volume_ma_20", "volume_ma20", "vol_ma_20", "vol_ma20"],
    "volume_ratio": ["volume_ratio", "volume_ratio_20", "vol_ratio"],
    "atr": ["atr_14", "atr14", "atr"],
    "atr_pct": ["atr_pct", "atr_percent", "atr_close_pct", "ATR_14_Pct"],
    "return_5d": ["return_5d", "ret_5d", "returns_5d"],
    "return_20d": ["return_20d", "ret_20d", "returns_20d"],
    "return_60d": ["return_60d", "ret_60d", "returns_60d"],
    "breakout": ["breakout", "breakout_20", "is_breakout", "Breakout_20D"],
    # 52-week high is approximately 252 trading days. Keep 52-day distance only as a last fallback.
    "dist_high": ["Distance_High_252_Pct", "distance_52w_high", "distance_high", "dist_high", "Distance_High_52_Pct"],
    "drawdown": ["Drawdown_252_Pct", "drawdown", "drawdown_252", "max_drawdown"],
    "volatility": ["Volatility_20_Annualized_Pct", "volatility_20d", "volatility", "vol_20d"],
    # Existing trend score expects the slope of closing price over 20 sessions.
    "trend_slope": ["Slope_Close_20", "trend_slope", "slope_20", "trend_slope_20", "Slope_SMA20_10"],
    "regime": ["technical_regime", "regime"],
    "avg_value": ["avg_value_20", "average_value_20", "value_ma20", "avg_traded_value", "Turnover_MA_20"],
    "gap": ["gap_pct", "gap"],
    "close_location": ["close_location_value", "clv"],
    "body_pct": ["candle_body_pct_range", "body_pct_range"],
    "upper_wick_pct": ["candle_upper_wick_pct_range", "upper_wick_pct_range"],
}


def norm_name(value: str) -> str:
    value = str(value).strip().lower()
    value = value.replace("%", " pct ")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def find_column(df: pd.DataFrame, key: str) -> str | None:
    normalized = {norm_name(c): c for c in df.columns}
    for alias in ALIASES[key]:
        candidate = norm_name(alias)
        if candidate in normalized:
            return normalized[candidate]
    return None


def number_series(df: pd.DataFrame, key: str, default=np.nan) -> pd.Series:
    col = find_column(df, key)
    if col is None:
        return pd.Series(default, index=df.index, dtype=float)
    raw = df[col].astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False)
    return pd.to_numeric(raw, errors="coerce")


def text_series(df: pd.DataFrame, key: str) -> pd.Series:
    col = find_column(df, key)
    if col is None:
        return pd.Series("", index=df.index, dtype=str)
    return df[col].fillna("").astype(str)


def boolean_series(df: pd.DataFrame, key: str) -> pd.Series:
    col = find_column(df, key)
    if col is None:
        return pd.Series(False, index=df.index)
    raw = df[col]
    numeric = pd.to_numeric(raw, errors="coerce")
    text = raw.fillna("").astype(str).str.lower().str.strip()
    return numeric.fillna(0).ne(0) | text.isin({"true", "yes", "y", "breakout", "1"})


def candidate_content_hash(df_or_path: pd.DataFrame | Path) -> str:
    if isinstance(df_or_path, Path):
        if not df_or_path.exists() or df_or_path.stat().st_size == 0:
            return ""
        try:
            df = pd.read_csv(df_or_path, low_memory=False)
        except Exception:
            return ""
    else:
        df = df_or_path.copy()
    cols = [c for c in df.columns if c not in LINEAGE_COLUMNS]
    return dataframe_hash(df[cols] if cols else df)


def score_candidates(df: pd.DataFrame, min_avg_value: float) -> pd.DataFrame:
    """Score technical quality and pre-entry readiness separately.

    Technical quality answers whether the stock is structurally attractive.
    Entry readiness answers whether the current location and trigger are suitable
    for a swing entry. Keeping both dimensions prevents a strong trend from being
    treated as immediately executable when price is already extended.
    """
    symbol_col = find_column(df, "symbol")
    if symbol_col is None:
        raise ValueError("Kolom Symbol/Emiten/Ticker/Code tidak ditemukan.")

    out = df.copy()
    out["Symbol"] = out[symbol_col].astype(str).str.upper().str.strip().str.replace(".JK", "", regex=False)

    open_price = number_series(out, "open")
    high = number_series(out, "high")
    low = number_series(out, "low")
    close = number_series(out, "close")
    sma5 = number_series(out, "sma5")
    sma20 = number_series(out, "sma20")
    sma50 = number_series(out, "sma50")
    sma200 = number_series(out, "sma200")
    ema20 = number_series(out, "ema20")
    rsi = number_series(out, "rsi")
    macd = number_series(out, "macd")
    signal = number_series(out, "macd_signal")
    hist = number_series(out, "macd_hist")
    volume = number_series(out, "volume")
    volume_ma20 = number_series(out, "volume_ma20")
    volume_ratio = number_series(out, "volume_ratio")
    atr = number_series(out, "atr")
    atr_pct = number_series(out, "atr_pct")
    ret5 = number_series(out, "return_5d")
    ret20 = number_series(out, "return_20d")
    ret60 = number_series(out, "return_60d")
    trend_slope = number_series(out, "trend_slope")
    dist_high = number_series(out, "dist_high")
    drawdown = number_series(out, "drawdown")
    volatility = number_series(out, "volatility")
    avg_value = number_series(out, "avg_value")
    gap = number_series(out, "gap")
    close_location = number_series(out, "close_location")
    body_pct = number_series(out, "body_pct")
    upper_wick_pct = number_series(out, "upper_wick_pct")
    dist_ema20 = number_series(out, "dist_ema20")
    breakout = boolean_series(out, "breakout")
    regime = text_series(out, "regime").str.lower()

    volume_ratio = volume_ratio.where(volume_ratio.notna(), volume / volume_ma20.replace(0, np.nan))
    atr_pct = atr_pct.where(atr_pct.notna(), atr / close.replace(0, np.nan) * 100)
    dist_ema20 = dist_ema20.where(dist_ema20.notna(), (close / ema20.replace(0, np.nan) - 1) * 100)
    candle_range = (high - low).replace(0, np.nan)
    close_location = close_location.where(close_location.notna(), (close - low) / candle_range)
    body_pct = body_pct.where(body_pct.notna(), (close - open_price).abs() / candle_range * 100)
    upper_wick_pct = upper_wick_pct.where(
        upper_wick_pct.notna(),
        (high - pd.concat([open_price, close], axis=1).max(axis=1)) / candle_range * 100,
    )

    # Normalize percentage-like fields exported as decimal fractions.
    for series in (ret5, ret20, ret60, dist_high, drawdown, volatility, gap):
        mask = series.abs().le(1.5)
        series.loc[mask] = series.loc[mask] * 100

    # Quality score: structurally strong, liquid, and reasonably controlled risk.
    score_trend = pd.Series(0.0, index=out.index)
    score_trend += np.where(close > sma20, 6, 0)
    score_trend += np.where(sma5 > sma20, 5, 0)
    score_trend += np.where(sma20 > sma50, 7, 0)
    score_trend += np.where(sma50 > sma200, 5, 0)
    score_trend += np.where(trend_slope > 0, 3, 0)
    score_trend += np.where(regime.str.contains("bullish", na=False), 4, 0)
    score_trend = score_trend.clip(upper=30)

    score_momentum = pd.Series(0.0, index=out.index)
    score_momentum += np.select(
        [
            rsi.between(50, 65, inclusive="both"),
            rsi.between(65, 75, inclusive="right"),
            rsi.between(40, 50, inclusive="left"),
            rsi.between(30, 40, inclusive="left"),
            rsi.between(75, 80, inclusive="right"),
        ],
        [10, 8, 6, 2, 3],
        default=0,
    )
    score_momentum += np.where(macd > signal, 5, 0)
    score_momentum += np.where(hist > 0, 3, 0)
    score_momentum += np.where(ret5 > 0, 1, 0)
    score_momentum += np.where(ret20 > 0, 1, 0)
    score_momentum = score_momentum.clip(upper=20)

    score_volume = pd.Series(0.0, index=out.index)
    score_volume += np.select(
        [volume_ratio.between(1.2, 3.5, inclusive="both"), volume_ratio.ge(0.8), volume_ratio.gt(3.5)],
        [8, 4, 6],
        default=0,
    )
    score_volume += np.where((ret5 > 0) & (volume_ratio >= 1.2), 3, 0)
    score_volume += np.where(breakout, 4, 0)
    score_volume = score_volume.clip(upper=15)

    score_position = pd.Series(0.0, index=out.index)
    abs_dist_ema = dist_ema20.abs()
    score_position += np.select(
        [abs_dist_ema <= 2.5, abs_dist_ema <= 5, abs_dist_ema <= 8],
        [5, 3, 1],
        default=0,
    )
    score_position += np.where(breakout, 3, 0)
    score_position += np.select(
        [dist_high.between(-10, 0, inclusive="both"), dist_high.between(-20, -10, inclusive="left")],
        [2, 1],
        default=0,
    )
    score_position = score_position.clip(upper=10)

    score_risk = pd.Series(0.0, index=out.index)
    score_risk += np.select(
        [atr_pct.between(1, 5, inclusive="both"), atr_pct.lt(1), atr_pct.between(5, 8, inclusive="right")],
        [9, 7, 4],
        default=0,
    )
    score_risk += np.where(drawdown > -20, 3, 0)
    score_risk += np.where(volatility < 60, 3, 0)
    score_risk = score_risk.clip(upper=15)

    score_liquidity = pd.Series(0.0, index=out.index)
    if avg_value.notna().any():
        score_liquidity += np.select(
            [avg_value >= 50e9, avg_value >= 20e9, avg_value >= 10e9, avg_value >= 5e9, avg_value >= min_avg_value],
            [10, 8, 6, 4, 2],
            default=0,
        )
    else:
        score_liquidity += 5
    score_liquidity = score_liquidity.clip(upper=10)

    technical_quality = (
        score_trend + score_momentum + score_volume + score_position + score_risk + score_liquidity
    ).clip(0, 100).round(2)

    # Pre-entry readiness: timing, trigger, current location, and candle confirmation.
    atr_extension = (dist_ema20.abs() / atr_pct.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    trend_context = (close > sma20) & (sma20 > sma50)
    breakout_valid = breakout & volume_ratio.ge(1.2) & rsi.le(76) & dist_ema20.le(8)
    pullback_valid = (
        trend_context
        & dist_ema20.between(-2.0, 3.0, inclusive="both")
        & rsi.between(45, 68, inclusive="both")
        & hist.ge(0)
    )
    continuation_valid = (
        trend_context
        & (macd > signal)
        & hist.gt(0)
        & dist_ema20.between(0, 6, inclusive="both")
    )
    early_accumulation_valid = (
        ~breakout_valid
        & ~pullback_valid
        & ~continuation_valid
        & close.ge(sma50)
        & dist_ema20.abs().le(4.5)
        & rsi.between(42, 64, inclusive="both")
        & hist.ge(-0.15 * atr.abs())
        & ret20.ge(-2)
        & volume_ratio.between(0.65, 2.2, inclusive="both")
    )
    bullish_close = (close > open_price) | close_location.ge(0.65)

    setup_type = pd.Series(
        np.select(
            [breakout_valid, pullback_valid, continuation_valid, early_accumulation_valid],
            ["BREAKOUT", "PULLBACK", "TREND_CONTINUATION", "EARLY_ACCUMULATION"],
            default="DEVELOPING",
        ),
        index=out.index,
    )

    readiness_context = pd.Series(0.0, index=out.index)
    readiness_context += np.where(close > sma20, 5, 0)
    readiness_context += np.where(sma20 > sma50, 7, 0)
    readiness_context += np.where(sma50 > sma200, 5, 0)
    readiness_context += np.where(trend_slope > 0, 3, 0)
    readiness_context += np.where(macd > signal, 5, 0)
    readiness_context = readiness_context.clip(upper=25)

    readiness_trigger = pd.Series(0.0, index=out.index)
    readiness_trigger += np.select(
        [breakout_valid, pullback_valid, continuation_valid, hist.gt(0)],
        [22, 20, 14, 5],
        default=0,
    )
    readiness_trigger += np.where(bullish_close, 3, 0)
    readiness_trigger = readiness_trigger.clip(upper=25)

    readiness_location = pd.Series(0.0, index=out.index)
    readiness_location += np.select(
        [abs_dist_ema <= 1.5, abs_dist_ema <= 3, abs_dist_ema <= 5, abs_dist_ema <= 7],
        [15, 12, 8, 4],
        default=0,
    )
    readiness_location += np.select(
        [atr_extension <= 1.5, atr_extension <= 2.0, atr_extension <= 2.5],
        [10, 6, 3],
        default=0,
    )
    readiness_location = readiness_location.clip(upper=25)

    readiness_confirmation = pd.Series(0.0, index=out.index)
    readiness_confirmation += np.select(
        [volume_ratio.between(1.2, 3.5, inclusive="both"), volume_ratio.ge(0.8), volume_ratio.gt(3.5)],
        [8, 4, 6],
        default=0,
    )
    readiness_confirmation += np.where(close_location >= 0.65, 4, 0)
    readiness_confirmation += np.where(body_pct >= 35, 3, 0)
    readiness_confirmation = readiness_confirmation.clip(upper=15)

    readiness_risk = pd.Series(0.0, index=out.index)
    readiness_risk += np.select(
        [atr_pct.between(1, 6, inclusive="both"), atr_pct.lt(1), atr_pct.between(6, 8, inclusive="right")],
        [5, 4, 2],
        default=0,
    )
    readiness_risk += np.where(rsi.between(45, 75, inclusive="both"), 3, 0)
    readiness_risk += np.where(gap.abs() <= 3, 2, 0)
    readiness_risk = readiness_risk.clip(upper=10)

    readiness_penalty = pd.Series(0.0, index=out.index)
    readiness_penalty += np.where((dist_ema20 > 8) | (atr_extension > 2.5), 20, 0)
    readiness_penalty += np.where(rsi > 78, 12, 0)
    readiness_penalty += np.where((volume_ratio > 5) & (rsi > 72), 8, 0)
    readiness_penalty += np.where(upper_wick_pct > 50, 8, 0)
    readiness_penalty += np.where(gap.abs() > 7, 8, 0)

    entry_readiness = (
        readiness_context
        + readiness_trigger
        + readiness_location
        + readiness_confirmation
        + readiness_risk
        - readiness_penalty
    ).clip(0, 100).round(2)

    hard_blocker = close.le(0) | rsi.gt(82) | atr_pct.gt(10)
    hard_reason = pd.Series(
        np.select(
            [close.le(0), rsi.gt(82), atr_pct.gt(10)],
            ["INVALID_PRICE", "RSI_ABOVE_HARD_LIMIT", "ATR_ABOVE_HARD_LIMIT"],
            default="",
        ),
        index=out.index,
    )
    soft_warning = pd.Series(
        np.select(
            [dist_ema20.gt(8) | atr_extension.gt(2.5), upper_wick_pct.gt(50), (volume_ratio > 5) & (rsi > 72)],
            ["PRICE_EXTENDED", "LONG_UPPER_WICK", "VOLUME_SPIKE_OVERHEATED"],
            default="",
        ),
        index=out.index,
    )

    out["Score_Trend"] = score_trend
    out["Score_Momentum"] = score_momentum
    out["Score_Volume"] = score_volume
    out["Score_Price_Position"] = score_position
    out["Score_Risk"] = score_risk
    out["Score_Liquidity"] = score_liquidity
    out["Technical_Quality_Score"] = technical_quality
    out["Technical_Score"] = technical_quality  # compatibility alias
    out["Entry_Readiness_PreScore"] = entry_readiness
    out["Entry_Readiness_Class"] = np.select(
        [entry_readiness >= 75, entry_readiness >= 60, entry_readiness >= 45],
        ["READY_ZONE", "DEVELOPING", "EARLY"],
        default="NOT_READY",
    )
    out["Setup_Type"] = setup_type
    out["Distance_EMA20_Pct"] = dist_ema20.round(4)
    out["ATR_Extension"] = atr_extension.round(4)
    out["Entry_Hard_Blocker"] = hard_blocker
    out["Entry_Hard_Blocker_Reason"] = hard_reason
    out["Entry_Soft_Warning"] = soft_warning

    hard_pass = (
        ~out["Symbol"].isin(EXCLUDED)
        & out["Symbol"].str.fullmatch(r"[A-Z0-9]{2,12}", na=False)
        & close.gt(0)
        & rsi.between(30, 82, inclusive="both")
        & atr_pct.le(10)
    )
    if avg_value.notna().any():
        hard_pass &= avg_value.ge(min_avg_value)

    out["Candidate_Status"] = np.where(hard_pass, "PASS", "FILTERED")
    rejection_lists: list[str] = []
    for idx in out.index:
        rejected: list[str] = []
        if out.loc[idx, "Symbol"] in EXCLUDED:
            rejected.append("EXCLUDED_SYMBOL")
        if not bool(pd.Series([out.loc[idx, "Symbol"]]).str.fullmatch(r"[A-Z0-9]{2,12}", na=False).iloc[0]):
            rejected.append("INVALID_SYMBOL")
        if close.loc[idx] <= 0 or pd.isna(close.loc[idx]):
            rejected.append("INVALID_PRICE")
        if not (30 <= rsi.loc[idx] <= 82):
            rejected.append("RSI_OUTSIDE_CANDIDATE_RANGE")
        if atr_pct.loc[idx] > 10:
            rejected.append("ATR_ABOVE_CANDIDATE_LIMIT")
        if avg_value.notna().any() and avg_value.loc[idx] < min_avg_value:
            rejected.append("LIQUIDITY_BELOW_MINIMUM")
        rejection_lists.append(json.dumps(rejected, ensure_ascii=False))
    out["Candidate_Rejected_By"] = rejection_lists
    out["Setup_Label"] = np.select(
        [technical_quality >= 82, technical_quality >= 72, technical_quality >= 60],
        ["PRIORITY", "STRONG", "WATCH"],
        default="WEAK",
    )

    reasons: list[str] = []
    for idx in out.index:
        tags: list[str] = []
        if score_trend.loc[idx] >= 22:
            tags.append("trend kuat")
        if 50 <= rsi.loc[idx] <= 75:
            tags.append("RSI sehat")
        if hist.loc[idx] > 0:
            tags.append("MACD positif")
        if volume_ratio.loc[idx] >= 1.2:
            tags.append("volume mendukung")
        if setup_type.loc[idx] != "DEVELOPING":
            tags.append(str(setup_type.loc[idx]).lower().replace("_", " "))
        warning = str(soft_warning.loc[idx])
        if warning:
            tags.append(warning.lower().replace("_", " "))
        reasons.append("; ".join(dict.fromkeys(tags)) if tags else "setup masih berkembang")
    out["Candidate_Reason"] = reasons

    preferred = [
        "Symbol", "Technical_Quality_Score", "Entry_Readiness_PreScore", "Entry_Readiness_Class",
        "Setup_Type", "Entry_Hard_Blocker", "Entry_Hard_Blocker_Reason", "Entry_Soft_Warning",
        "Technical_Score", "Setup_Label", "Candidate_Status", "Candidate_Rejected_By", "Candidate_Reason",
        "Score_Trend", "Score_Momentum", "Score_Volume", "Score_Price_Position",
        "Score_Risk", "Score_Liquidity", "Distance_EMA20_Pct", "ATR_Extension",
    ]
    remaining = [c for c in out.columns if c not in preferred]
    result = out[preferred + remaining].copy()
    result["_candidate_order"] = result["Candidate_Status"].map({"PASS": 0, "FILTERED": 1}).fillna(9)
    result = result.sort_values(
        ["_candidate_order", "Technical_Quality_Score", "Entry_Readiness_PreScore"],
        ascending=[True, False, False],
    )
    result["Relative_Rank_Pct"] = (
        result["Technical_Quality_Score"].rank(method="min", ascending=False, pct=True) * 100
    ).round(2)
    result["Candidate_Decision_Trace"] = result.apply(
        lambda row: json.dumps([
            f"STATUS={row['Candidate_Status']}",
            f"SETUP={row['Setup_Type']}",
            f"TECH={float(row['Technical_Quality_Score']):.1f}",
            f"READINESS={float(row['Entry_Readiness_PreScore']):.1f}",
            f"RANK_PCT={float(row['Relative_Rank_Pct']):.1f}",
        ], ensure_ascii=False),
        axis=1,
    )
    return result.drop(columns="_candidate_order")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pilih kandidat saham Indonesia dari latest_technical_features.csv"
    )
    parser.add_argument("input", help="Path latest_technical_features.csv")
    parser.add_argument("--top", type=int, default=None, help="Jumlah kandidat untuk Broker Summary")
    parser.add_argument(
        "--min-score", type=float, default=None,
        help="Technical score minimum untuk kandidat"
    )
    parser.add_argument(
        "--min-avg-value", type=float, default=1_000_000_000,
        help="Minimum rata-rata nilai transaksi; diterapkan bila kolom tersedia"
    )
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--output-dir", default="candidate_output")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest-dir", default=None)
    parser.add_argument("--data-quality-status", default="VALID")
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    cfg, config_provenance = load_runtime_config(config_path, strict=config_path.name.lower() == "pipeline.json")
    candidate_cfg = cfg.get("candidate", {})
    args.top = int(args.top if args.top is not None else candidate_cfg.get("top", 40))
    args.min_score = float(args.min_score if args.min_score is not None else candidate_cfg.get("min_score", 58))
    max_relative_rank_pct = float(candidate_cfg.get("max_relative_rank_pct", 15.0))
    setup_min_scores = candidate_cfg.get("setup_min_scores", {})

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: file tidak ditemukan: {input_path}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else output_dir.parent / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_path, low_memory=False)
    if df.empty:
        print(f"ERROR: input technical kosong: {input_path}", file=sys.stderr)
        return 2
    if "Date" in df.columns:
        technical_data_date = pd.to_datetime(df["Date"], errors="coerce").max()
    elif "Latest_Valid_Candle_Date" in df.columns:
        technical_data_date = pd.to_datetime(df["Latest_Valid_Candle_Date"], errors="coerce").max()
    else:
        technical_data_date = pd.NaT
    technical_date_text = technical_data_date.date().isoformat() if pd.notna(technical_data_date) else ""
    source_hash = file_sha256(input_path)
    ranking = score_candidates(df, args.min_avg_value)
    generated_at = datetime.now().isoformat(timespec="seconds")
    lineage_cols = {
        "Run_ID": args.run_id,
        "Technical_Source_Path": str(input_path.resolve()),
        "Technical_Source_Hash": source_hash,
        "Technical_Data_Date": technical_date_text,
        "Candidate_Generated_At": generated_at,
        "Data_Quality_Status": args.data_quality_status,
        "Config_Source": config_provenance.get("config_source", ""),
        "Config_Hash": config_provenance.get("config_hash", ""),
        "Config_Version": config_provenance.get("config_version", ""),
    }
    for col, value in lineage_cols.items():
        ranking[col] = value

    setup_defaults = {
        "BREAKOUT": 60,
        "PULLBACK": 58,
        "TREND_CONTINUATION": 60,
        "EARLY_ACCUMULATION": 56,
        "DEVELOPING": 62,
    }
    effective_setup_min = {
        key: max(args.min_score, float(setup_min_scores.get(key, default)))
        for key, default in setup_defaults.items()
    }
    setup_minimum = ranking["Setup_Type"].map(effective_setup_min).fillna(effective_setup_min["DEVELOPING"])
    eligible = ranking[
        (ranking["Candidate_Status"] == "PASS")
        & (ranking["Technical_Quality_Score"] >= setup_minimum)
        & (ranking["Relative_Rank_Pct"] <= max_relative_rank_pct)
    ].head(args.top).copy()

    if eligible.empty:
        print("WARNING: tidak ada kandidat yang lolos. Coba turunkan --min-score.")
    broker_symbols = eligible[["Symbol", "Technical_Quality_Score", "Entry_Readiness_PreScore", "Setup_Type", "Setup_Label", "Candidate_Reason"]].copy()
    for col, value in lineage_cols.items():
        broker_symbols[col] = value

    ranking_path = output_dir / "technical_ranking_full.csv"
    candidates_path = output_dir / f"technical_candidates_top{args.top}.csv"
    broker_path = output_dir / "broker_symbols.csv"
    manifest_path = output_dir / "manifest.json"
    previous_candidate_hash = candidate_content_hash(candidates_path) if candidates_path.exists() else ""
    previous_candidate_count = 0
    if candidates_path.exists() and candidates_path.stat().st_size > 0:
        try:
            previous_candidate_count = len(pd.read_csv(candidates_path, low_memory=False))
        except Exception:
            previous_candidate_count = 0

    ranking.to_csv(ranking_path, index=False)
    eligible.to_csv(candidates_path, index=False)
    broker_symbols.to_csv(broker_path, index=False)
    candidate_hash = candidate_content_hash(eligible)
    broker_hash = file_sha256(broker_path)
    candidate_changed = bool(previous_candidate_hash and previous_candidate_hash != candidate_hash)
    if not previous_candidate_hash:
        candidate_changed = True
    reason = "" if candidate_changed else "RESULT_IDENTICAL_AFTER_FRESH_RECALCULATION"

    manifest = {
        "Run_ID": args.run_id,
        "input": str(input_path.resolve()),
        "Technical_Source_Path": str(input_path.resolve()),
        "Technical_Source_Hash": source_hash,
        "Technical_Data_Date": technical_date_text,
        "Technical_Generated_At": str(df.get("Technical_Generated_At", pd.Series([""])).dropna().iloc[0]) if "Technical_Generated_At" in df.columns and not df["Technical_Generated_At"].dropna().empty else "",
        "Candidate_Generated_At": generated_at,
        "rows_input": int(len(df)),
        "rows_ranked": int(len(ranking)),
        "rows_candidates": int(len(eligible)),
        "Candidate_Count": int(len(eligible)),
        "Candidate_Symbols": eligible["Symbol"].astype(str).tolist(),
        "Candidate_Hash": candidate_hash,
        "Previous_Candidate_Hash": previous_candidate_hash,
        "Candidate_Changed": candidate_changed,
        "Previous_Candidate_Count": previous_candidate_count,
        "Broker_Symbols_Path": str(broker_path.resolve()),
        "Broker_Symbols_Hash": broker_hash,
        "Broker_Navigator_Path": "",
        "Broker_Navigator_Hash": "",
        "Data_Quality_Status": args.data_quality_status,
        "Config_Source": config_provenance.get("config_source", ""),
        "Config_Hash": config_provenance.get("config_hash", ""),
        "Config_Version": config_provenance.get("config_version", ""),
        "Config_Override_Mode": config_provenance.get("override_mode", "NONE"),
        "Effective_Max_Relative_Rank_Pct": max_relative_rank_pct,
        "Effective_Setup_Min_Scores": effective_setup_min,
        "Status": "OK",
        "Warning": "",
        "Reason": reason,
        "top_n": args.top,
        "min_score": args.min_score,
        "min_avg_value": args.min_avg_value,
        "outputs": {
            "ranking": str(ranking_path),
            "candidates": str(candidates_path),
            "broker_symbols": str(broker_path),
        },
    }
    write_json(manifest_path, manifest)
    write_json(manifest_dir / f"CANDIDATE_MANIFEST_{args.run_id}.json", manifest)

    print(f"Input       : {len(df)} saham")
    print(f"Kandidat    : {len(eligible)} saham")
    print(f"Candidate changed: {candidate_changed}")
    print(f"Ranking     : {ranking_path}")
    print(f"Top kandidat: {candidates_path}")
    print(f"Broker CSV  : {broker_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
