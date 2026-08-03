#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {_norm(col): col for col in frame.columns}
    for alias in aliases:
        found = mapping.get(_norm(alias))
        if found is not None:
            return found
    return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = gain / loss.mask(loss.eq(0))
    result = 100 - (100 / (1 + rs))
    result = result.where(~((gain > 0) & loss.eq(0)), 100.0)
    result = result.where(~(gain.eq(0) & (loss > 0)), 0.0)
    result = result.where(~(gain.eq(0) & loss.eq(0)), 50.0)
    return result


def _load_ihsg(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()
    date_col = _column(raw, "Date", "Datetime")
    close_col = _column(raw, "Close", "Adj Close", "Adj_Close")
    if not date_col or not close_col:
        return pd.DataFrame()
    result = pd.DataFrame({
        "Date": pd.to_datetime(raw[date_col], errors="coerce"),
        "Close": pd.to_numeric(raw[close_col], errors="coerce"),
    })
    for field in ("Open", "High", "Low", "Volume"):
        source = _column(raw, field)
        result[field] = pd.to_numeric(raw[source], errors="coerce") if source else pd.NA
    return (
        result.dropna(subset=["Date", "Close"])
        .sort_values("Date")
        .drop_duplicates(subset=["Date"], keep="last")
        .reset_index(drop=True)
    )


def _breadth(path: Path | None, ihsg_date: date) -> dict[str, Any]:
    empty = {
        "technical_date": None,
        "above_sma20_pct": None,
        "above_sma50_pct": None,
        "macd_bullish_pct": None,
        "valid_symbol_count": 0,
    }
    if path is None or not path.exists() or path.stat().st_size == 0:
        return empty
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return empty
    if frame.empty:
        return empty
    date_col = _column(frame, "Date", "Latest Valid Candle Date", "Technical Data Date")
    if date_col:
        parsed = pd.to_datetime(frame[date_col], errors="coerce")
        eligible = parsed.notna() & (parsed.dt.date <= ihsg_date)
        if eligible.any():
            latest = parsed.loc[eligible].max()
            frame = frame.loc[parsed.dt.date == latest.date()].copy()
            technical_date = latest.date()
        else:
            technical_date = None
    else:
        technical_date = None
    if technical_date and abs((ihsg_date - technical_date).days) > 4:
        return {**empty, "technical_date": technical_date.isoformat(), "warning": "TECHNICAL_BREADTH_STALE"}

    def percentage(*aliases: str) -> float | None:
        col = _column(frame, *aliases)
        if not col:
            return None
        numeric = pd.to_numeric(frame[col], errors="coerce").dropna()
        if numeric.empty:
            return None
        if numeric.max() <= 1.0 and numeric.min() >= 0.0:
            return float(numeric.mean() * 100.0)
        close_col = _column(frame, "Close")
        reference_col = None
        for alias in aliases:
            if "sma20" in _norm(alias):
                reference_col = _column(frame, "SMA 20", "SMA_20")
            elif "sma50" in _norm(alias):
                reference_col = _column(frame, "SMA 50", "SMA_50")
        if close_col and reference_col:
            close = pd.to_numeric(frame[close_col], errors="coerce")
            ref = pd.to_numeric(frame[reference_col], errors="coerce")
            valid = close.notna() & ref.notna()
            if valid.any():
                return float((close[valid] > ref[valid]).mean() * 100.0)
        return None

    return {
        "technical_date": technical_date.isoformat() if technical_date else None,
        "above_sma20_pct": percentage("Above SMA20", "Above_SMA20"),
        "above_sma50_pct": percentage("Above SMA50", "Above_SMA50"),
        "macd_bullish_pct": percentage("MACD Bullish", "MACD_Bullish"),
        "valid_symbol_count": int(len(frame)),
    }


def _classify(score: float) -> str:
    if score >= 7.0:
        return "STRONG BULLISH"
    if score >= 4.0:
        return "BULLISH"
    if score >= 1.5:
        return "EARLY BULLISH"
    if score > -1.5:
        return "SIDEWAYS"
    if score > -4.0:
        return "EARLY BEARISH"
    if score > -7.0:
        return "BEARISH"
    return "STRONG BEARISH"


def _reason(regime: str, close: float, ma20: float, ma50: float, slope20: float, rsi14: float, breadth20: float | None) -> str:
    if close > ma20 and ma20 > ma50:
        structure = "harga IHSG berada di atas MA20 dan MA50"
    elif close > ma20 and ma20 <= ma50:
        structure = "harga IHSG sudah di atas MA20, tetapi MA20 belum melewati MA50"
    elif close <= ma20 and ma20 > ma50:
        structure = "tren menengah masih positif, tetapi harga sedang berada di bawah MA20"
    else:
        structure = "harga IHSG berada di bawah MA20 dan MA50"

    momentum = "momentum menguat" if slope20 > 0 and rsi14 >= 52 else "momentum melemah" if slope20 < 0 and rsi14 <= 48 else "momentum masih campuran"
    if breadth20 is None:
        breadth = "breadth belum tersedia"
    elif breadth20 >= 60:
        breadth = "mayoritas saham berada di atas SMA20"
    elif breadth20 <= 40:
        breadth = "breadth pasar masih lemah"
    else:
        breadth = "breadth pasar belum dominan"
    return f"{structure}; {momentum}; {breadth}."


def calculate_market_outlook_regime(
    ihsg_path: Path,
    technical_path: Path | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Calculate a presentation-only IHSG regime for Market Outlook.

    This does not modify Decision Engine scoring. It uses the latest available
    IHSG close plus momentum and optional market breadth so mixed structures are
    not automatically collapsed into SIDEWAYS.
    """
    frame = _load_ihsg(ihsg_path)
    reference_date = as_of_date or date.today()
    if not frame.empty:
        frame = frame.loc[frame["Date"].dt.date <= reference_date].reset_index(drop=True)
    default = {
        "market_regime": "UNKNOWN",
        "regime_score": 0.0,
        "confidence_pct": 0.0,
        "data_date": None,
        "data_age_days": None,
        "is_stale": True,
        "reason": "Data IHSG tidak tersedia atau belum cukup untuk menghitung regime.",
        "source": str(ihsg_path),
        "warnings": ["IHSG_DATA_NOT_AVAILABLE"],
    }
    if len(frame) < 60:
        return {**default, "warnings": [f"IHSG_ROWS_INSUFFICIENT:{len(frame)}"]}

    close_series = frame["Close"].astype(float)
    frame["MA20"] = close_series.rolling(20).mean()
    frame["MA50"] = close_series.rolling(50).mean()
    frame["MA200"] = close_series.rolling(200).mean()
    frame["EMA12"] = close_series.ewm(span=12, adjust=False).mean()
    frame["EMA26"] = close_series.ewm(span=26, adjust=False).mean()
    frame["MACD"] = frame["EMA12"] - frame["EMA26"]
    frame["MACD_SIGNAL"] = frame["MACD"].ewm(span=9, adjust=False).mean()
    frame["MACD_HIST"] = frame["MACD"] - frame["MACD_SIGNAL"]
    frame["RSI14"] = _rsi(close_series, 14)

    latest = frame.iloc[-1]
    latest_date = pd.Timestamp(latest["Date"]).date()
    age_days = max(0, (reference_date - latest_date).days)
    close = float(latest["Close"])
    ma20 = float(latest["MA20"])
    ma50 = float(latest["MA50"])
    ma200 = _float(latest["MA200"])
    previous_ma20 = float(frame["MA20"].iloc[-6])
    previous_ma50 = float(frame["MA50"].iloc[-11])
    slope20 = _pct_change(ma20, previous_ma20) or 0.0
    slope50 = _pct_change(ma50, previous_ma50) or 0.0
    rsi14 = float(latest["RSI14"])
    macd_hist = float(latest["MACD_HIST"])
    return5 = _pct_change(close, float(frame["Close"].iloc[-6])) or 0.0
    return20 = _pct_change(close, float(frame["Close"].iloc[-21])) or 0.0
    breadth = _breadth(technical_path, latest_date)
    breadth20 = _float(breadth.get("above_sma20_pct"))
    breadth50 = _float(breadth.get("above_sma50_pct"))

    score = 0.0
    score += 2.0 if close > ma20 else -2.0
    score += 1.0 if close > ma50 else -1.0
    score += 2.0 if ma20 > ma50 else -2.0
    score += 1.5 if slope20 > 0 else -1.5
    score += 0.5 if slope50 > 0 else -0.5
    score += 1.0 if rsi14 >= 55 else -1.0 if rsi14 <= 45 else 0.0
    score += 1.0 if macd_hist > 0 else -1.0
    score += 0.5 if return5 >= 1.0 else -0.5 if return5 <= -1.0 else 0.0
    if breadth20 is not None:
        score += 1.0 if breadth20 >= 60 else -1.0 if breadth20 <= 40 else 0.0
    if breadth50 is not None:
        score += 0.5 if breadth50 >= 55 else -0.5 if breadth50 <= 45 else 0.0
    if ma200 is not None:
        score += 0.5 if close > ma200 else -0.5

    regime = _classify(score)
    full_bull_structure = close > ma20 > ma50 and slope20 > 0
    full_bear_structure = close < ma20 < ma50 and slope20 < 0
    if regime in {"STRONG BULLISH", "BULLISH"} and not full_bull_structure:
        regime = "EARLY BULLISH"
    elif regime in {"STRONG BEARISH", "BEARISH"} and not full_bear_structure:
        regime = "EARLY BEARISH"
    completeness = 1.0 if breadth20 is not None else 0.9
    confidence = min(95.0, 48.0 + abs(score) * 5.0) * completeness
    warnings: list[str] = []
    is_stale = age_days > 4
    if is_stale:
        confidence = max(20.0, confidence - 25.0)
        warnings.append(f"IHSG_DATA_STALE:{age_days}D")
    if breadth.get("warning"):
        warnings.append(str(breadth["warning"]))

    return {
        "market_regime": regime,
        "regime_score": round(score, 2),
        "confidence_pct": round(confidence, 1),
        "data_date": latest_date.isoformat(),
        "data_age_days": age_days,
        "is_stale": is_stale,
        "ihsg_close": round(close, 4),
        "ma20": round(ma20, 4),
        "ma50": round(ma50, 4),
        "ma200": round(ma200, 4) if ma200 is not None else None,
        "ma20_slope_5d_pct": round(slope20, 3),
        "ma50_slope_10d_pct": round(slope50, 3),
        "rsi14": round(rsi14, 2),
        "macd_hist": round(macd_hist, 4),
        "return_5d_pct": round(return5, 2),
        "return_20d_pct": round(return20, 2),
        # These labels are presentation facts derived from the same IHSG
        # measurements above.  They do not feed Decision Engine scoring.
        "ihsg_change_pct": round(_pct_change(close, float(frame["Close"].iloc[-2])) or 0.0, 3),
        "trend": (
            "BULLISH" if close > ma20 > ma50
            else "BEARISH" if close < ma20 < ma50
            else "MIXED"
        ),
        "momentum": (
            "POSITIVE" if slope20 > 0 and rsi14 >= 52 and macd_hist >= 0
            else "NEGATIVE" if slope20 < 0 and rsi14 <= 48 and macd_hist <= 0
            else "MIXED"
        ),
        "breadth": (
            "BULLISH" if breadth20 is not None and breadth20 >= 60
            else "BEARISH" if breadth20 is not None and breadth20 <= 40
            else "MIXED" if breadth20 is not None
            else "INSUFFICIENT_DATA"
        ),
        "execution_mode": (
            "SELECTIVE AGGRESSIVE" if regime in {"STRONG BULLISH", "BULLISH"}
            else "DEFENSIVE" if regime in {"STRONG BEARISH", "BEARISH"}
            else "SELECTIVE"
        ),
        "breadth_above_sma20_pct": round(breadth20, 1) if breadth20 is not None else None,
        "breadth_above_sma50_pct": round(breadth50, 1) if breadth50 is not None else None,
        "breadth_macd_bullish_pct": round(float(breadth["macd_bullish_pct"]), 1) if breadth.get("macd_bullish_pct") is not None else None,
        "breadth_symbol_count": int(breadth.get("valid_symbol_count") or 0),
        "technical_date": breadth.get("technical_date"),
        "reason": _reason(regime, close, ma20, ma50, slope20, rsi14, breadth20),
        "source": str(ihsg_path),
        "warnings": warnings,
        "calculated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "MARKET_OUTLOOK_PRESENTATION_ONLY",
    }


def save_market_outlook_regime(status: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
