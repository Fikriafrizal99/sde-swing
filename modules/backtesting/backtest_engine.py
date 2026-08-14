#!/usr/bin/env python3
from __future__ import annotations

"""Lifecycle-consistent facade for the baseline backtest engine."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from typing import Any

import numpy as np
import pandas as pd

from modules.backtesting import backtest_engine_baseline as _baseline
from modules.analytics.execution_integrity import validate_plan_geometry
from modules.analytics.lifecycle_contract import (
    LIFECYCLE_CONTRACT_VERSION,
    evaluate_trade_path,
    first_trigger,
    lifecycle_state_dict,
    prepare_lifecycle_bars,
    trigger_spec_from_plan,
)
from modules.analytics.replay_contract import replay_report_columns

_baseline_evaluate_signal = _baseline.evaluate_signal

for _name in dir(_baseline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_baseline, _name)


def load_price(path: Path) -> pd.DataFrame:
    return prepare_lifecycle_bars(pd.read_csv(path, low_memory=False))


def attach_entry_plans(signals: pd.DataFrame, entry_plans_path: Path | None) -> pd.DataFrame:
    if not entry_plans_path or not entry_plans_path.exists() or entry_plans_path.stat().st_size == 0:
        return signals
    plans = pd.read_csv(entry_plans_path, low_memory=False)
    if plans.empty:
        return signals
    signal_symbol = _baseline.require_col(signals, "Symbol")
    plan_symbol = _baseline.require_col(plans, "Symbol")
    keep_aliases = [
        "Symbol", "Reference_Close", "Entry_Price", "Entry_Reference_Price",
        "Entry_Zone_Low", "Entry_Zone_High", "Initial_Stop", "Stop_Loss",
        "Target_1", "Take_Profit_1", "Target_2", "Take_Profit_2",
        "Plan_Status", "Rejection_Reason", "Minor_Resistance",
        "Nearest_Resistance", "Setup_Type", "Max_Hold_Days",
    ]
    keep = [_baseline.find_col(plans, name) for name in keep_aliases]
    keep = list(dict.fromkeys(c for c in keep if c))
    plans = plans[keep].copy().drop_duplicates(plan_symbol, keep="last")
    plans[plan_symbol] = (
        plans[plan_symbol].astype(str).str.upper().str.strip().str.replace(".JK", "", regex=False)
    )
    return signals.merge(
        plans,
        how="left",
        left_on=signal_symbol,
        right_on=plan_symbol,
        suffixes=("", "_Plan"),
    )


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if np.isfinite(number) else None
    except Exception:
        return None


def _return_pct(price: float | None, entry: float | None) -> float | None:
    if price is None or entry is None or entry == 0:
        return None
    return (price / entry - 1.0) * 100.0


def evaluate_signal(signal, px, horizons, entry_mode, max_hold_days):
    px = prepare_lifecycle_bars(px)
    base = {**signal.to_dict(), **replay_report_columns()}
    if px.empty:
        return {**base, "Status": "NO_FUTURE_PRICE"}

    signal_date = pd.Timestamp(signal["Signal_Date"])
    plan = {str(k): v for k, v in signal.items()}
    setup = str(signal.get("Setup_Type", "") or "")
    trigger_type, trigger_price = trigger_spec_from_plan(plan, setup)
    stop = _num(signal.get("Initial_Stop", signal.get("Stop_Loss", np.nan)))
    tp1 = _num(signal.get("Target_1", signal.get("Take_Profit_1", np.nan)))
    tp2 = _num(signal.get("Target_2", signal.get("Take_Profit_2", np.nan)))
    configured_hold = int(_num(signal.get("Max_Hold_Days")) or max_hold_days or 20)

    entry_idx: int | None = None
    raw_entry_price: float | None = None
    executable_plan = trigger_type != "INVALID" and None not in (stop, tp1, tp2)

    if executable_plan:
        post_signal = px[px["Date"] > signal_date].copy().reset_index(drop=True)
        trigger = first_trigger(
            post_signal,
            trigger_type=trigger_type,
            trigger_price=trigger_price,
            entry_zone_low=_num(signal.get("Entry_Zone_Low")),
            entry_zone_high=_num(signal.get("Entry_Zone_High")),
            expiry_sessions=7,
        )
        if trigger is None:
            return {
                **base,
                "Status": "WAITING_TRIGGER" if len(post_signal) < 7 else "EXPIRED",
                "Final_Outcome": "OPEN",
                "Final_Outcome_D7": "OPEN",
                "Lifecycle_Contract_Version": LIFECYCLE_CONTRACT_VERSION,
                "TP1_Hit": False,
                "TP2_Hit": False,
                "SL_Hit": False,
            }
        relative_idx, raw_entry_price = trigger
        entry_date = post_signal.loc[relative_idx, "Date"]
        positions = px.index[px["Date"] == entry_date].tolist()
        if not positions:
            return {**base, "Status": "NO_FUTURE_PRICE"}
        entry_idx = int(positions[0])
    else:
        candidates = (
            px.index[px["Date"] > signal_date]
            if entry_mode == "next_open"
            else px.index[px["Date"] >= signal_date]
        )
        if len(candidates) == 0:
            return {**base, "Status": "NO_FUTURE_PRICE"}
        entry_idx = int(candidates[0])
        raw_entry_price = float(
            px.loc[entry_idx, "Open"] if entry_mode == "next_open" else px.loc[entry_idx, "Close"]
        )

    slippage_pct = pd.to_numeric(
        pd.Series([signal.get("Estimated_Slippage_Pct", signal.get("Liquidity_Estimated_Slippage_Pct", 0.0))]),
        errors="coerce",
    ).fillna(0.0).iloc[0]
    entry_price = float(raw_entry_price)
    slippage_adjusted_entry = entry_price * (1.0 + max(float(slippage_pct), 0.0) / 100.0)
    result = {
        **base,
        "Status": "OK",
        "Entry_Date": px.loc[entry_idx, "Date"],
        "Raw_Entry_Price": raw_entry_price,
        "Entry_Price": entry_price,
        "Slippage_Pct": slippage_pct,
        "Slippage_Adjusted_Entry_Price": slippage_adjusted_entry,
        "Stop_Loss": stop,
        "Take_Profit_1": tp1,
        "Take_Profit_2": tp2,
        "Lifecycle_Contract_Version": LIFECYCLE_CONTRACT_VERSION,
    }

    for h in horizons:
        exit_idx = entry_idx + h
        if exit_idx < len(px):
            window = px.loc[entry_idx:exit_idx]
            gross = (float(px.loc[exit_idx, "Close"]) / entry_price - 1) * 100
            result[f"Return_{h}D_Pct"] = gross
            result[f"MFE_{h}D_Pct"] = (window["High"].max() / entry_price - 1) * 100
            result[f"MAE_{h}D_Pct"] = (window["Low"].min() / entry_price - 1) * 100
            result[f"Exit_Date_{h}D"] = px.loc[exit_idx, "Date"]
        else:
            result[f"Return_{h}D_Pct"] = np.nan
            result[f"MFE_{h}D_Pct"] = np.nan
            result[f"MAE_{h}D_Pct"] = np.nan

    for h in (1, 3, 5, 7):
        idx = entry_idx + h
        close = float(px.loc[idx, "Close"]) if idx < len(px) else None
        result[f"Close_D{h}"] = close if close is not None else np.nan
        value = _return_pct(close, entry_price)
        result[f"Return_D{h}_Pct"] = value if value is not None else np.nan

    if executable_plan:
        geometry = validate_plan_geometry(entry_price, stop, tp1, tp2)
        if not geometry.valid:
            return {
                **result,
                "Status": "INVALID_PLAN_AT_ENTRY",
                "Final_Outcome": "OPEN",
                "Final_Outcome_D7": "OPEN",
                "Exit_Reason": "PLAN_GEOMETRY_INVALID_AT_ENTRY:" + "+".join(geometry.reasons),
            }
        eval_start = entry_idx + 1 if trigger_type == "CLOSE_ABOVE" else entry_idx
        state = evaluate_trade_path(
            px.iloc[eval_start:].copy().reset_index(drop=True),
            entry_price=entry_price,
            initial_stop=float(stop),
            target_1=float(tp1),
            target_2=float(tp2),
            max_hold_days=configured_hold,
        )
        lifecycle = lifecycle_state_dict(state)
        result.update({
            "Final_Outcome": lifecycle["final_outcome"],
            "Final_Outcome_D7": lifecycle["final_outcome"],
            "Exit_Reason": lifecycle["exit_reason"],
            "Exit_Date": lifecycle["exit_date"],
            "Exit_Price": lifecycle["exit_price"],
            "Holding_Days": lifecycle["holding_days"],
            "Holding_Period_Days": lifecycle["holding_days"],
            "TP1_Hit": bool(lifecycle["tp1_hit"]),
            "TP2_Hit": bool(lifecycle["tp2_hit"]),
            "SL_Hit": bool(lifecycle["sl_hit"]),
            "TP1_Hit_D7": bool(lifecycle["tp1_hit"]),
            "TP2_Hit_D7": bool(lifecycle["tp2_hit"]),
            "SL_Hit_D7": bool(lifecycle["sl_hit"]),
            "MFE_D7_Pct": lifecycle["mfe_pct"],
            "MAE_D7_Pct": lifecycle["mae_pct"],
            "Max_Price_D7": lifecycle["max_price"],
            "Min_Price_D7": lifecycle["min_price"],
            "Realized_Return_Pct": lifecycle["realized_return_pct"],
        })
        risk_pct = ((entry_price - stop) / entry_price * 100.0) if stop is not None and entry_price > stop else np.nan
        result["Initial_Risk_Pct"] = risk_pct
        realized = lifecycle["realized_return_pct"]
        result["Return_R"] = realized / risk_pct if realized is not None and pd.notna(risk_pct) and risk_pct > 0 else np.nan
        result["Return_R_D7"] = result["Return_R"]
        result["MFE_R_D7"] = lifecycle["mfe_pct"] / risk_pct if pd.notna(risk_pct) and risk_pct > 0 and lifecycle["mfe_pct"] is not None else np.nan
        result["MAE_R_D7"] = lifecycle["mae_pct"] / risk_pct if pd.notna(risk_pct) and risk_pct > 0 and lifecycle["mae_pct"] is not None else np.nan
        result["False_Positive"] = bool(
            signal.get("Gated_Decision") in _baseline.ENTRY_DECISIONS
            and lifecycle["final_outcome"] == "LOSS"
        )
        future_return = result.get("Return_D7_Pct")
        result["False_Negative"] = bool(
            signal.get("Gated_Decision") not in _baseline.ENTRY_DECISIONS
            and pd.notna(future_return)
            and future_return >= 3.0
        )
        last_idx = min(entry_idx + configured_hold, len(px) - 1)
        result["MaxHold_Exit_Date"] = px.loc[last_idx, "Date"]
        result["MaxHold_Return_Pct"] = (
            float(px.loc[last_idx, "Close"]) / entry_price - 1
        ) * 100
        return result

    legacy = _baseline_evaluate_signal(signal, px, horizons, entry_mode, max_hold_days)
    legacy["Lifecycle_Contract_Version"] = "LEGACY_NO_EXECUTABLE_PLAN"
    legacy.update(replay_report_columns())
    return legacy


_baseline.load_price = load_price
_baseline.attach_entry_plans = attach_entry_plans
_baseline.evaluate_signal = evaluate_signal

if __name__ == "__main__":
    raise SystemExit(_baseline.main())
