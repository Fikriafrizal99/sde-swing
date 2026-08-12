#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

"""Lifecycle-consistent facade for the baseline Exit Engine.

Entry-plan generation and all price calculations remain in exit_engine_baseline
byte-for-byte. Only active-trade lifecycle state semantics are unified.
"""

import pandas as pd

from modules.exit_engine import exit_engine_baseline as _baseline
from modules.analytics.lifecycle_contract import LIFECYCLE_CONTRACT_VERSION

for _name in dir(_baseline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_baseline, _name)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "hit"}


def _same_session(left, right) -> bool:
    left_ts = pd.to_datetime(left, errors="coerce")
    right_ts = pd.to_datetime(right, errors="coerce")
    if pd.isna(left_ts) or pd.isna(right_ts):
        return False
    return left_ts.date() == right_ts.date()


def update_active_trade(
    trade: pd.Series,
    decision_row: pd.Series | None,
    px: pd.DataFrame,
    max_hold_days: int,
) -> tuple[dict, dict | None]:
    latest = px.iloc[-1]
    current_close = float(latest["Close"])
    current_low = float(latest["Low"])
    current_high = float(latest["High"])
    ema20 = float(latest["EMA20"]) if not pd.isna(latest["EMA20"]) else _baseline.np.nan
    atr = float(latest["ATR14"]) if not pd.isna(latest["ATR14"]) else _baseline.np.nan

    entry_price = _baseline.to_num(trade.get("Entry_Price"))
    initial_stop = _baseline.to_num(trade.get("Initial_Stop"))
    current_stop = _baseline.to_num(trade.get("Current_Stop"), initial_stop)
    target1 = _baseline.to_num(trade.get("Target_1"))
    target2 = _baseline.to_num(trade.get("Target_2"))
    highest_close = max(_baseline.to_num(trade.get("Highest_Close"), entry_price), current_close)
    prior_holding_days = int(_baseline.to_num(trade.get("Holding_Days"), 0))
    same_session = _same_session(trade.get("Last_Update"), latest["Date"])
    holding_days = prior_holding_days if same_session else prior_holding_days + 1

    prior_tp1 = _truthy(trade.get("TP1_Hit", False))
    tp1_hit = prior_tp1
    tp1_hit_date = str(trade.get("TP1_Hit_Date", "") or "")
    trailing_active = _truthy(trade.get("Trailing_Active", prior_tp1))

    reasons: list[str] = []
    exit_price = _baseline.np.nan

    stop_hit = current_low <= current_stop
    target2_hit = current_high >= target2
    target1_hit_now = current_high >= target1

    if stop_hit:
        reasons.append("STOP_LOSS")
        if target1_hit_now or target2_hit:
            reasons.append("AMBIGUOUS_BAR_STOP_PRIORITY")
        exit_price = current_stop
    elif target2_hit:
        if not tp1_hit:
            tp1_hit = True
            tp1_hit_date = str(latest["Date"])
        trailing_active = True
        reasons.append("TARGET_2")
        exit_price = target2
    elif target1_hit_now and not tp1_hit:
        tp1_hit = True
        tp1_hit_date = str(latest["Date"])
        trailing_active = True
        reasons.append("TARGET_1_HIT_TRAIL_ACTIVE")

    broker = str(
        decision_row.get("Broker_Confirmation", "") if decision_row is not None else ""
    ).upper()
    decision = str(
        decision_row.get("Decision", "") if decision_row is not None else ""
    ).upper()

    if broker in _baseline.BROKER_DISTRIBUTION:
        reasons.append("BROKER_DISTRIBUTION")
    if not _baseline.math.isnan(ema20) and current_close < ema20:
        reasons.append("CLOSE_BELOW_EMA20")
    if decision in {"AVOID", "SPECULATIVE"}:
        reasons.append(f"DECISION_DOWNGRADE_{decision}")
    if holding_days >= max_hold_days:
        reasons.append(f"MAX_HOLD_{max_hold_days}D")

    risk = entry_price - initial_stop
    r_multiple = (current_close - entry_price) / risk if risk > 0 else 0
    new_stop = current_stop
    if r_multiple >= 1.0:
        new_stop = max(new_stop, entry_price)
    if r_multiple >= 1.5 and not _baseline.math.isnan(ema20) and not _baseline.math.isnan(atr):
        new_stop = max(new_stop, ema20 - 0.5 * atr)

    hard_exit = any(
        x in reasons
        for x in ["STOP_LOSS", "TARGET_2", "BROKER_DISTRIBUTION", "CLOSE_BELOW_EMA20"]
    ) or any(
        x.startswith("DECISION_DOWNGRADE_") or x.startswith("MAX_HOLD_")
        for x in reasons
    )

    updated = trade.to_dict()
    updated.update({
        "Current_Stop": new_stop,
        "Highest_Close": highest_close,
        "Holding_Days": holding_days,
        "Last_Close": current_close,
        "Last_Update": latest["Date"],
        "Last_Decision": decision or trade.get("Last_Decision", ""),
        "Last_Broker_Confirmation": broker or trade.get("Last_Broker_Confirmation", ""),
        "TP1_Hit": bool(tp1_hit),
        "TP1_Hit_Date": tp1_hit_date,
        "Trailing_Active": bool(trailing_active),
        "Lifecycle_Contract_Version": LIFECYCLE_CONTRACT_VERSION,
    })

    alert = None
    if hard_exit:
        if _baseline.math.isnan(exit_price):
            exit_price = current_close
        pnl_pct = (exit_price / entry_price - 1) * 100 if entry_price else _baseline.np.nan
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
            "TP1_Hit": bool(tp1_hit),
            "Trailing_Active": bool(trailing_active),
            "Lifecycle_Contract_Version": LIFECYCLE_CONTRACT_VERSION,
        }
    else:
        updated["Status"] = "ACTIVE"
        updated["Exit_Reason"] = ""
        if "TARGET_1_HIT_TRAIL_ACTIVE" in reasons:
            updated["Lifecycle_Last_Event"] = "TARGET_1_HIT_TRAIL_ACTIVE"
    return updated, alert


_baseline.update_active_trade = update_active_trade

if __name__ == "__main__":
    raise SystemExit(_baseline.main())
