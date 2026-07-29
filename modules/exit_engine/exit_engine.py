#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import file_sha256, make_run_id, write_json


PLAN_DECISIONS = {"STRONG BUY", "BUY", "BUY CANDIDATE", "WATCH HIGH"}
BROKER_DISTRIBUTION = {"DISTRIBUTION", "STRONG DISTRIBUTION"}
BROKER_ACCUMULATION = {"ACCUMULATION", "STRONG ACCUMULATION"}


def norm_col(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


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


def to_num(v: Any, default=np.nan) -> float:
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(str(v).replace(",", ""))
    except Exception:
        return default


def load_decisions(path: Path) -> pd.DataFrame:
    """Load V3 decisions into stable canonical columns without duplicate labels.

    FINAL_DECISION_V3 intentionally retains the preceding V2 columns for lineage.
    Canonical V3 fields therefore overwrite the working aliases instead of renaming
    columns and accidentally producing duplicate column names.
    """
    df = pd.read_csv(path, low_memory=False)

    required = {
        "Symbol": ("Symbol", "Ticker", "EMITEN"),
        "Decision": ("Decision_V3", "Decision"),
    }
    optional = {
        "Final_Score": ("Final_Score_V3", "Final_Score"),
        "Technical_Score": ("Technical_Score_Final", "Technical_Score"),
        "Broker_Score": ("Broker_Score",),
        "Broker_Confirmation": ("Broker_Confirmation",),
        "Liquidity_Class": ("Liquidity_Class",),
        "Market_Regime": ("Market_Regime",),
        "Technical_Quality_Score": ("Technical_Quality_Score_Final", "Technical_Quality_Score", "Technical_Score_Final"),
        "Entry_Readiness_PreScore": ("Entry_Readiness_PreScore_Final", "Entry_Readiness_PreScore"),
        "Setup_Type": ("Setup_Type",),
        "Entry_Hard_Blocker": ("Entry_Hard_Blocker",),
        "Entry_Soft_Warning": ("Entry_Soft_Warning",),
        "Broker_Confidence": ("Broker_Confidence_Final", "Broker_Confidence"),
        "Broker_Direction": ("Broker_Direction_Final", "Broker_Direction"),
        "Broker_Divergence": ("Broker_Divergence",),
        "RSI_14": ("RSI_14",),
        "Volume_Ratio_20": ("Volume_Ratio_20",),
        "Distance_EMA20_Pct": ("Distance_EMA20_Pct", "Distance_EMA_20_Pct"),
        "ATR_Extension": ("ATR_Extension",),
    }

    for target, aliases in required.items():
        source = require_col(df, *aliases)
        df[target] = df[source]
    for target, aliases in optional.items():
        source = find_col(df, *aliases)
        if source is not None:
            df[target] = df[source]

    df["Symbol"] = (
        df["Symbol"]
        .astype(str)
        .str.upper()
        .str.strip()
        .str.replace(".JK", "", regex=False)
    )
    df["Decision"] = df["Decision"].astype(str).str.upper().str.strip()
    return df


def index_price_files(price_dir: Path) -> dict[str, Path]:
    out = {}
    for file in price_dir.rglob("*.csv"):
        try:
            sample = pd.read_csv(file, nrows=3)
        except Exception:
            continue
        if not all(find_col(sample, x) for x in ["date", "open", "high", "low", "close"]):
            continue
        symbol_col = find_col(sample, "symbol", "ticker", "emiten")
        symbol = (
            str(sample[symbol_col].iloc[0]).upper().strip()
            if symbol_col and len(sample)
            else file.stem.upper()
        )
        out[symbol.replace(".JK", "")] = file
    return out


def load_price(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    rename = {
        require_col(df, "date", "datetime", "timestamp"): "Date",
        require_col(df, "open"): "Open",
        require_col(df, "high"): "High",
        require_col(df, "low"): "Low",
        require_col(df, "close"): "Close",
    }
    for target, aliases in {
        "Volume": ("volume",),
        "Value": ("value", "turnover"),
    }.items():
        col = find_col(df, *aliases)
        if col:
            rename[col] = target
    df = df.rename(columns=rename)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for c in ["Open", "High", "Low", "Close", "Volume", "Value"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"])
    df = df.sort_values("Date").drop_duplicates("Date").reset_index(drop=True)

    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14).mean()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA10"] = df["Close"].ewm(span=10, adjust=False).mean()
    df["VolMA20"] = df["Volume"].rolling(20).mean() if "Volume" in df else np.nan
    return df


def recent_support(px: pd.DataFrame, lookback: int = 20) -> float:
    window = px.tail(lookback)
    if window.empty:
        return np.nan
    # Conservative support proxy: recent swing low excluding the latest candle.
    lows = window["Low"].iloc[:-1] if len(window) > 1 else window["Low"]
    return float(lows.min())


def determine_stop(entry: float, support: float, atr: float, max_risk_pct: float) -> tuple[float, str]:
    candidates = []
    if not math.isnan(support) and support < entry:
        candidates.append((support, "SUPPORT"))
    if not math.isnan(atr) and atr > 0:
        atr_stop = entry - 1.5 * atr
        if atr_stop < entry:
            candidates.append((atr_stop, "ATR_1.5X"))

    floor = entry * (1 - max_risk_pct / 100)
    # Pick the tighter valid technical stop, but never wider than max risk.
    valid = [(max(price, floor), label) for price, label in candidates if price > 0]
    if not valid:
        return floor, "MAX_RISK_CAP"
    stop, label = max(valid, key=lambda x: x[0])
    return stop, label


def resistance_levels(px: pd.DataFrame, entry: float, lookback: int = 120) -> list[float]:
    """Return clustered confirmed swing-high resistance levels above entry."""
    window = px.tail(lookback).copy()
    if len(window) < 7:
        return []
    high = window["High"]
    pivot_mask = (
        (high.shift(1) < high) & (high.shift(2) <= high)
        & (high.shift(-1) < high) & (high.shift(-2) <= high)
    )
    raw = sorted(float(value) for value in window.loc[pivot_mask, "High"] if float(value) > entry * 1.001)
    clustered: list[float] = []
    for level in raw:
        if not clustered or abs(level / clustered[-1] - 1) > 0.01:
            clustered.append(level)
        else:
            clustered[-1] = max(clustered[-1], level)
    return clustered


def nearest_resistance(px: pd.DataFrame, entry: float, lookback: int = 60) -> float:
    levels = resistance_levels(px, entry, max(lookback, 60))
    return levels[0] if levels else np.nan


def should_build_plan(row: pd.Series) -> bool:
    decision = str(row.get("Decision", "")).upper()
    broker_direction = str(row.get("Broker_Direction", "")).upper()
    broker_confirmation = str(row.get("Broker_Confirmation", "")).upper()
    if broker_direction == "DISTRIBUTION" or "STRONG DISTRIBUTION" in broker_confirmation:
        return False
    if decision in PLAN_DECISIONS:
        return True
    if decision != "WATCH":
        return False
    quality = to_num(row.get("Technical_Quality_Score"), to_num(row.get("Technical_Score"), 0.0))
    readiness = to_num(row.get("Entry_Readiness_PreScore"), 0.0)
    liquidity = str(row.get("Liquidity_Class", "")).upper()
    return quality >= 72 and readiness >= 55 and liquidity not in {"THIN", "ILLIQUID"}


def build_entry_plan(row: pd.Series, px: pd.DataFrame, min_rr: float, preferred_rr: float, max_risk_pct: float, max_hold_days: int) -> dict:
    latest = px.iloc[-1]
    reference_close = float(latest["Close"])
    atr = to_num(latest.get("ATR14"))
    ema20 = to_num(latest.get("EMA20"))

    setup_type = str(row.get("Setup_Type", "DEVELOPING") or "DEVELOPING").upper()
    previous_high_20 = float(px["High"].iloc[-21:-1].max()) if len(px) >= 21 else np.nan
    if setup_type == "BREAKOUT" and not math.isnan(previous_high_20) and not math.isnan(atr):
        entry_low = max(previous_high_20, reference_close - 0.50 * atr)
        entry_high = reference_close + 0.25 * atr
    elif setup_type == "PULLBACK" and not math.isnan(ema20) and not math.isnan(atr):
        entry_low = ema20 - 0.25 * atr
        entry_high = min(reference_close * 1.01, ema20 + 0.35 * atr)
    else:
        entry_low = reference_close * 0.99
        entry_high = reference_close * 1.01

    if entry_low > entry_high:
        entry_low, entry_high = entry_high, entry_low

    # Use the upper edge of the planned entry zone as a conservative execution
    # reference. Risk, targets, and resistance RR must not be calculated from the
    # current close when the strategy is explicitly waiting for a lower pullback.
    planned_entry = float(entry_high)
    support = recent_support(px, 20)
    stop, stop_basis = determine_stop(planned_entry, support, atr, max_risk_pct)
    risk = planned_entry - stop
    risk_pct = (risk / planned_entry) * 100 if planned_entry else np.nan

    rr1_target = planned_entry + risk * min_rr
    rr2_target = planned_entry + risk * preferred_rr
    resistance_floor = min(reference_close, entry_low)
    levels = resistance_levels(px, resistance_floor, 120)
    minor_resistance = levels[0] if levels else np.nan
    minor_rr = (minor_resistance - planned_entry) / risk if levels and risk > 0 else np.nan
    major_resistance = next(
        (level for level in levels if risk > 0 and (level - planned_entry) / risk >= min_rr),
        np.nan,
    )
    major_rr = (major_resistance - planned_entry) / risk if not math.isnan(major_resistance) and risk > 0 else np.nan

    prelim = to_num(row.get("Entry_Readiness_PreScore"), 50.0)
    quality = to_num(row.get("Technical_Quality_Score"), to_num(row.get("Technical_Score"), 0.0))
    dist_ema20 = to_num(
        row.get("Distance_EMA20_Pct"),
        (reference_close / ema20 - 1) * 100 if ema20 and not math.isnan(ema20) else np.nan,
    )
    atr_pct = (atr / reference_close * 100) if reference_close and not math.isnan(atr) else np.nan
    atr_extension = to_num(row.get("ATR_Extension"), np.nan)
    if math.isnan(atr_extension):
        atr_extension = abs(dist_ema20) / atr_pct if atr_pct and not math.isnan(dist_ema20) else np.nan
    price_to_zone = 0.0
    price_position_to_zone = "IN_ZONE"
    if reference_close < entry_low:
        price_to_zone = (entry_low / reference_close - 1) * 100
        price_position_to_zone = "BELOW_ZONE"
    elif reference_close > entry_high:
        price_to_zone = (reference_close / entry_high - 1) * 100
        price_position_to_zone = "ABOVE_ZONE"

    readiness_final = prelim
    readiness_final += 10 if risk_pct <= 5 else 5 if risk_pct <= max_risk_pct else -15
    readiness_final += 12 if (math.isnan(major_rr) and math.isnan(minor_rr)) else 10 if not math.isnan(major_rr) and major_rr >= preferred_rr else 6 if not math.isnan(major_rr) else -8
    readiness_final += 10 if price_to_zone <= 0.5 else 5 if price_to_zone <= 2.0 else -10
    readiness_final += 5 if setup_type in {"BREAKOUT", "PULLBACK", "TREND_CONTINUATION"} else 0
    if not math.isnan(minor_rr) and minor_rr < min_rr:
        readiness_final -= 8
    if not math.isnan(atr_extension) and atr_extension > 2.5:
        readiness_final -= 20
    readiness_final = float(np.clip(readiness_final, 0, 100))

    hard_blocker = str(row.get("Entry_Hard_Blocker", "")).strip().lower() in {"1", "true", "yes"}
    liquidity = str(row.get("Liquidity_Class", "")).upper()
    market = str(row.get("Market_Regime", "")).upper()
    setup_quality = "ACCEPT"
    rejection_reason = ""
    warnings: list[str] = []

    if risk <= 0:
        setup_quality, rejection_reason = "REJECT", "INVALID_STOP"
    elif market in {"BEAR", "BEARISH"}:
        setup_quality, rejection_reason = "REJECT", "BEARISH_MARKET_REGIME"
    elif liquidity in {"ILLIQUID", "THIN"}:
        setup_quality, rejection_reason = "REJECT", "LIQUIDITY_GATE"
    elif hard_blocker:
        setup_quality, rejection_reason = "REJECT", "ENTRY_HARD_BLOCKER"
    elif not math.isnan(atr_extension) and atr_extension > 2.5:
        setup_quality, rejection_reason = "REJECT", "PRICE_EXTENDED"
    elif not math.isnan(minor_rr) and minor_rr < min_rr and math.isnan(major_rr):
        setup_quality, rejection_reason = "REJECT", "NO_VALID_RESISTANCE_PATH"
    elif price_position_to_zone == "ABOVE_ZONE" and price_to_zone > 0.5:
        setup_quality, rejection_reason = "CONDITIONAL", "WAIT_FOR_ENTRY_ZONE"
    elif not math.isnan(minor_rr) and minor_rr < 0.60 and not math.isnan(major_rr):
        setup_quality, rejection_reason = "CONDITIONAL", "MINOR_RESISTANCE_NEAR"
    elif readiness_final >= 72 and price_to_zone <= 2.0:
        setup_quality, rejection_reason = "ACCEPT", ""
    elif readiness_final >= 58:
        setup_quality = "CONDITIONAL"
        rejection_reason = "MINOR_RESISTANCE_NEAR" if not math.isnan(minor_rr) and minor_rr < min_rr else "WAIT_FOR_ENTRY_TRIGGER"
    else:
        setup_quality, rejection_reason = "REJECT", "ENTRY_READINESS_BELOW_MINIMUM"

    # Final readiness must remain semantically consistent with the plan status.
    # A setup waiting for a trigger must not display 100% readiness, and a
    # rejected plan must not retain a high pre-guardrail score.
    if setup_quality == "CONDITIONAL":
        readiness_final = min(readiness_final, 69.0)
    elif setup_quality == "REJECT":
        readiness_final = min(readiness_final, 49.0)

    if not math.isnan(minor_rr) and minor_rr < min_rr:
        warnings.append("minor resistance dekat")
    if price_to_zone > 2:
        warnings.append("harga di luar area entry")
    soft_warning = str(row.get("Entry_Soft_Warning", "")).strip()
    if soft_warning and soft_warning.lower() not in {"nan", "none", "null"}:
        warnings.append(soft_warning.replace("_", " ").lower())
    if str(row.get("Broker_Divergence", "")).strip().lower() in {"1", "true", "yes"}:
        warnings.append("broker divergence")

    decision = str(row.get("Decision", "WATCH")).upper()
    if setup_quality == "ACCEPT" and decision in {"STRONG BUY", "BUY"}:
        execution_status = "BUY CONFIRMED"
    elif setup_quality == "ACCEPT":
        execution_status = "BUY CANDIDATE READY"
    elif setup_quality == "CONDITIONAL":
        execution_status = "WAIT TRIGGER"
    else:
        execution_status = "NOT READY"

    return {
        "Symbol": row["Symbol"],
        "Decision": decision,
        "Execution_Status": execution_status,
        "Plan_Status": setup_quality,
        "Rejection_Reason": rejection_reason,
        "Plan_Warnings": "; ".join(dict.fromkeys(warnings)),
        "Setup_Type": setup_type,
        "Reference_Date": latest["Date"],
        "Reference_Close": reference_close,
        "Entry_Reference_Price": planned_entry,
        "Entry_Zone_Low": entry_low,
        "Entry_Zone_High": entry_high,
        "Price_To_Entry_Zone_Pct": price_to_zone,
        "Price_Position_To_Entry_Zone": price_position_to_zone,
        "Initial_Stop": stop,
        "Stop_Basis": stop_basis,
        "Risk_Per_Share": risk,
        "Risk_Pct": risk_pct,
        "Target_1": rr1_target,
        "Target_1_RR": min_rr,
        "Target_2": rr2_target,
        "Target_2_RR": preferred_rr,
        "Nearest_Resistance": minor_resistance,
        "Minor_Resistance": minor_resistance,
        "Major_Resistance": major_resistance,
        "RR_To_Resistance": minor_rr,
        "RR_To_Minor_Resistance": minor_rr,
        "RR_To_Major_Resistance": major_rr,
        "Entry_Readiness_PreScore": prelim,
        "Entry_Readiness_Final": readiness_final,
        "Technical_Quality_Score": quality,
        "EMA20": ema20,
        "ATR14": atr,
        "ATR_Extension": atr_extension,
        "Broker_Confirmation": row.get("Broker_Confirmation", "NEUTRAL"),
        "Broker_Direction": row.get("Broker_Direction", "NEUTRAL"),
        "Broker_Confidence": row.get("Broker_Confidence", np.nan),
        "Liquidity_Class": row.get("Liquidity_Class", "UNKNOWN"),
        "Market_Regime": row.get("Market_Regime", "UNKNOWN"),
        "Final_Score": row.get("Final_Score", np.nan),
        "Technical_Score": row.get("Technical_Score", np.nan),
        "Broker_Score": row.get("Broker_Score", np.nan),
        "Max_Hold_Days": max_hold_days,
    }


def load_state(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def update_active_trade(trade: pd.Series, decision_row: pd.Series | None, px: pd.DataFrame, max_hold_days: int) -> tuple[dict, dict | None]:
    latest = px.iloc[-1]
    current_close = float(latest["Close"])
    current_low = float(latest["Low"])
    current_high = float(latest["High"])
    ema20 = float(latest["EMA20"]) if not pd.isna(latest["EMA20"]) else np.nan
    atr = float(latest["ATR14"]) if not pd.isna(latest["ATR14"]) else np.nan

    entry_price = to_num(trade.get("Entry_Price"))
    current_stop = to_num(trade.get("Current_Stop"), to_num(trade.get("Initial_Stop")))
    target1 = to_num(trade.get("Target_1"))
    target2 = to_num(trade.get("Target_2"))
    highest_close = max(to_num(trade.get("Highest_Close"), entry_price), current_close)
    holding_days = int(to_num(trade.get("Holding_Days"), 0)) + 1

    reasons = []
    exit_price = np.nan

    stop_hit = current_low <= current_stop
    target2_hit = current_high >= target2
    target1_hit = current_high >= target1
    # Daily OHLC does not reveal which level was touched first. Use the conservative
    # assumption when stop and target occur in the same candle.
    if stop_hit:
        reasons.append("STOP_LOSS")
        if target1_hit or target2_hit:
            reasons.append("AMBIGUOUS_BAR_STOP_PRIORITY")
        exit_price = current_stop
    elif target2_hit:
        reasons.append("TARGET_2")
        exit_price = target2
    elif target1_hit:
        reasons.append("TARGET_1")
        exit_price = target1

    broker = str(decision_row.get("Broker_Confirmation", "") if decision_row is not None else "").upper()
    decision = str(decision_row.get("Decision", "") if decision_row is not None else "").upper()

    if broker in BROKER_DISTRIBUTION:
        reasons.append("BROKER_DISTRIBUTION")
    if not math.isnan(ema20) and current_close < ema20:
        reasons.append("CLOSE_BELOW_EMA20")
    if decision in {"AVOID", "SPECULATIVE"}:
        reasons.append(f"DECISION_DOWNGRADE_{decision}")
    if holding_days >= max_hold_days:
        reasons.append(f"MAX_HOLD_{max_hold_days}D")

    # Trailing logic: after >=1R, lift stop to breakeven; after >=1.5R, trail under EMA20 / 1ATR.
    risk = entry_price - to_num(trade.get("Initial_Stop"))
    r_multiple = (current_close - entry_price) / risk if risk > 0 else 0
    new_stop = current_stop
    if r_multiple >= 1.0:
        new_stop = max(new_stop, entry_price)
    if r_multiple >= 1.5 and not math.isnan(ema20) and not math.isnan(atr):
        new_stop = max(new_stop, ema20 - 0.5 * atr)

    hard_exit = any(x in reasons for x in [
        "STOP_LOSS", "TARGET_2", "BROKER_DISTRIBUTION", "CLOSE_BELOW_EMA20"
    ]) or any(x.startswith("DECISION_DOWNGRADE_") or x.startswith("MAX_HOLD_") for x in reasons)
    # TARGET_1 alone does not close the full trade; it activates trailing.
    if "TARGET_1" in reasons and not hard_exit:
        reasons = [x for x in reasons if x != "TARGET_1"]
        reasons.append("TARGET_1_HIT_TRAIL_ACTIVE")

    updated = trade.to_dict()
    updated.update({
        "Current_Stop": new_stop,
        "Highest_Close": highest_close,
        "Holding_Days": holding_days,
        "Last_Close": current_close,
        "Last_Update": latest["Date"],
        "Last_Decision": decision or trade.get("Last_Decision", ""),
        "Last_Broker_Confirmation": broker or trade.get("Last_Broker_Confirmation", ""),
    })

    alert = None
    if hard_exit:
        if math.isnan(exit_price):
            exit_price = current_close
        pnl_pct = (exit_price / entry_price - 1) * 100 if entry_price else np.nan
        updated["Status"] = "CLOSED"
        updated["Exit_Date"] = latest["Date"]
        updated["Exit_Price"] = exit_price
        updated["Exit_Reason"] = " | ".join(dict.fromkeys(reasons))
        updated["Return_Pct"] = pnl_pct
        alert = {
            "Symbol": trade["Symbol"],
            "Alert": "EXIT",
            "Exit_Date": latest["Date"],
            "Entry_Price": entry_price,
            "Exit_Price": exit_price,
            "Return_Pct": pnl_pct,
            "Holding_Days": holding_days,
            "Reason": updated["Exit_Reason"],
        }
    else:
        updated["Status"] = "ACTIVE"
        updated["Exit_Reason"] = ""
    return updated, alert


def main() -> int:
    p = argparse.ArgumentParser(description="Stockbit SDE Exit Engine V1.6")
    p.add_argument("decision_csv")
    p.add_argument("price_dir")
    p.add_argument("--state-file", default="state/ACTIVE_TRADES.csv")
    p.add_argument("--output-dir", default="exit_output")
    p.add_argument("--min-rr", type=float, default=1.0)
    p.add_argument("--preferred-rr", type=float, default=2.0)
    p.add_argument("--max-risk-pct", type=float, default=7.0)
    p.add_argument("--max-hold-days", type=int, default=20)
    p.add_argument("--open-approved", action="store_true",
                   help="Masukkan semua plan ACCEPT sebagai active trade pada harga reference close.")
    p.add_argument("--run-id", default=None)
    p.add_argument("--manifest-dir", default=None)
    p.add_argument("--data-quality-status", default="VALID")
    args = p.parse_args()
    args.run_id = args.run_id or make_run_id()

    decisions = load_decisions(Path(args.decision_csv))
    price_index = index_price_files(Path(args.price_dir))
    state_path = Path(args.state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    active = load_state(state_path)
    plans = []
    missing = []

    plan_candidates = decisions[decisions.apply(should_build_plan, axis=1)]
    for _, row in plan_candidates.iterrows():
        path = price_index.get(row["Symbol"])
        if path is None:
            missing.append(row["Symbol"])
            continue
        px = load_price(path)
        plans.append(build_entry_plan(row, px, args.min_rr, args.preferred_rr, args.max_risk_pct, args.max_hold_days))

    plans_df = pd.DataFrame(plans)
    if not plans_df.empty:
        plans_df["Run_ID"] = args.run_id
        plans_df["Data_Quality_Status"] = args.data_quality_status
    plans_df.to_csv(output / "ENTRY_PLANS.csv", index=False)
    if not plans_df.empty:
        plans_df[plans_df["Plan_Status"] == "ACCEPT"].to_csv(output / "APPROVED_ENTRIES.csv", index=False)
        plans_df[plans_df["Plan_Status"] == "CONDITIONAL"].to_csv(output / "CONDITIONAL_ENTRIES.csv", index=False)
        plans_df[plans_df["Plan_Status"] == "REJECT"].to_csv(output / "REJECTED_ENTRIES.csv", index=False)

    if args.open_approved and not plans_df.empty:
        existing_active = set(active.loc[active.get("Status", pd.Series(dtype=str)).astype(str).eq("ACTIVE"), "Symbol"]) if not active.empty else set()
        new_rows = []
        for _, plan in plans_df[plans_df["Plan_Status"] == "ACCEPT"].iterrows():
            if plan["Symbol"] in existing_active:
                continue
            new_rows.append({
                "Symbol": plan["Symbol"],
                "Entry_Date": plan["Reference_Date"],
                "Entry_Price": plan["Reference_Close"],
                "Initial_Stop": plan["Initial_Stop"],
                "Current_Stop": plan["Initial_Stop"],
                "Target_1": plan["Target_1"],
                "Target_2": plan["Target_2"],
                "Status": "ACTIVE",
                "Highest_Close": plan["Reference_Close"],
                "Holding_Days": 0,
                "Last_Update": plan["Reference_Date"],
                "Last_Decision": plan["Decision"],
                "Last_Broker_Confirmation": plan["Broker_Confirmation"],
            })
        if new_rows:
            active = pd.concat([active, pd.DataFrame(new_rows)], ignore_index=True)

    alerts = []
    updated_rows = []
    decision_map = {r["Symbol"]: r for _, r in decisions.iterrows()}

    for _, trade in active.iterrows():
        if str(trade.get("Status", "")).upper() != "ACTIVE":
            updated_rows.append(trade.to_dict())
            continue
        symbol = str(trade["Symbol"]).upper()
        path = price_index.get(symbol)
        if path is None:
            updated_rows.append(trade.to_dict())
            missing.append(symbol)
            continue
        px = load_price(path)
        updated, alert = update_active_trade(
            trade,
            decision_map.get(symbol),
            px,
            args.max_hold_days
        )
        updated_rows.append(updated)
        if alert:
            alerts.append(alert)

    state_df = pd.DataFrame(updated_rows)
    if not state_df.empty:
        state_df["Run_ID"] = args.run_id
        state_df["Data_Quality_Status"] = args.data_quality_status
    state_df.to_csv(state_path, index=False)
    state_df.to_csv(output / "ACTIVE_TRADES_STATE.csv", index=False)
    alerts_df = pd.DataFrame(alerts)
    if not alerts_df.empty:
        alerts_df["Run_ID"] = args.run_id
        alerts_df["Data_Quality_Status"] = args.data_quality_status
    alerts_df.to_csv(output / "EXIT_ALERTS.csv", index=False)
    pd.DataFrame({"Symbol": sorted(set(missing))}).to_csv(output / "MISSING_PRICE_SYMBOLS.csv", index=False)

    manifest = {
        "Run_ID": args.run_id,
        "version": "1.6.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision_source": str(Path(args.decision_csv).resolve()),
        "decision_source_hash": file_sha256(Path(args.decision_csv)),
        "price_dir": str(Path(args.price_dir).resolve()),
        "min_rr": args.min_rr,
        "preferred_rr": args.preferred_rr,
        "max_risk_pct": args.max_risk_pct,
        "max_hold_days": args.max_hold_days,
        "entry_rules": [
            "Plans are built for STRONG BUY, BUY, BUY CANDIDATE, WATCH HIGH, and qualified WATCH",
            "Technical quality and entry readiness are evaluated separately",
            "Liquidity THIN/ILLIQUID rejected",
            "Bearish market regime rejected when Market_Regime is supplied",
            "Minor resistance can produce CONDITIONAL when a valid major resistance path exists"
        ],
        "stop_rules": [
            "Recent support",
            "1.5 ATR",
            "Maximum risk cap"
        ],
        "exit_rules": [
            "Initial stop loss",
            "Target 2",
            "Close below EMA20",
            "Broker changes to distribution",
            "Decision downgrade",
            f"Maximum hold {args.max_hold_days} trading days"
        ],
        "trailing_rules": [
            "At +1R move stop to breakeven",
            "At +1.5R trail using EMA20 minus 0.5 ATR",
            "Target 1 activates trailing rather than mandatory full exit"
        ],
        "entry_plans": len(plans_df),
        "approved_entries": int((plans_df.get("Plan_Status", pd.Series(dtype=str)) == "ACCEPT").sum()) if not plans_df.empty else 0,
        "conditional_entries": int((plans_df.get("Plan_Status", pd.Series(dtype=str)) == "CONDITIONAL").sum()) if not plans_df.empty else 0,
        "exit_alerts": len(alerts),
        "active_state_rows": len(state_df),
        "Data_Quality_Status": args.data_quality_status,
        "outputs": {
            "entry_plans": str((output / "ENTRY_PLANS.csv").resolve()),
            "approved_entries": str((output / "APPROVED_ENTRIES.csv").resolve()),
            "conditional_entries": str((output / "CONDITIONAL_ENTRIES.csv").resolve()),
            "rejected_entries": str((output / "REJECTED_ENTRIES.csv").resolve()),
            "exit_alerts": str((output / "EXIT_ALERTS.csv").resolve()),
            "active_state": str((output / "ACTIVE_TRADES_STATE.csv").resolve()),
        },
    }
    write_json(output / "manifest.json", manifest)
    if args.manifest_dir:
        write_json(Path(args.manifest_dir) / f"EXIT_MANIFEST_{args.run_id}.json", manifest)

    print("Exit Engine V1.6 selesai")
    print(f"Entry plans : {len(plans_df)}")
    print(f"Approved    : {manifest['approved_entries']}")
    print(f"Conditional : {manifest['conditional_entries']}")
    print(f"Exit alerts : {len(alerts)}")
    print(f"State rows  : {len(state_df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
