#!/usr/bin/env python3
"""Conservative 1-5 session trigger backtest for SDE Swing audit.

Rules:
- Signals are generated after the signal-date close, so only later sessions are eligible.
- A plan triggers only when the planned Entry_Reference_Price trades inside a later
  daily high/low range. This is deliberately stricter than any zone intersection.
- If stop and target are both inside the same daily bar after triggering, stop is
  assumed first (conservative because intraday ordering is unavailable).
- Data ends 2026-07-31, so the maximum observable horizon is four sessions.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "data/output/audit_v162"
PRICE_DIR = ROOT / "data/output/historical/by_symbol"


def f(value: Any, default: float = math.nan) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def prices(symbol: str) -> pd.DataFrame:
    path = PRICE_DIR / f"{symbol}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, low_memory=False)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Date", "High", "Low", "Close"]).sort_values("Date")


def evaluate_plan(model: str, signal_date: str, symbol: str, decision: str,
                  entry: float, stop: float, target1: float, setup: str = "") -> dict[str, Any]:
    df = prices(symbol)
    future = df[df["Date"] > pd.Timestamp(signal_date)].head(5).copy() if not df.empty else pd.DataFrame()
    record: dict[str, Any] = {
        "Model": model, "Signal_Date": signal_date, "Symbol": symbol,
        "Decision": decision, "Setup_Type": setup, "Entry": entry,
        "Stop": stop, "Target_1": target1, "Available_Forward_Days": len(future),
        "Triggered": False, "Trigger_Date": "", "Trigger_Delay_Sessions": math.nan,
        "Outcome": "UNVALIDATED" if future.empty else "NOT_TRIGGERED",
        "Exit_Date": "", "Realized_R": math.nan, "Realized_Return_Pct": math.nan,
        "MFE_After_Trigger_Pct": math.nan, "MAE_After_Trigger_Pct": math.nan,
        "Stop_Hit": False, "Target1_Hit": False,
    }
    if future.empty or not all(math.isfinite(x) for x in (entry, stop, target1)) or entry <= stop:
        return record

    trigger_positions = future.index[(future["Low"] <= entry) & (future["High"] >= entry)].tolist()
    if not trigger_positions:
        return record
    trigger_idx = trigger_positions[0]
    trigger_loc = future.index.get_loc(trigger_idx)
    after = future.iloc[trigger_loc:].copy()
    record["Triggered"] = True
    record["Trigger_Date"] = after.iloc[0]["Date"].date().isoformat()
    record["Trigger_Delay_Sessions"] = trigger_loc + 1
    record["MFE_After_Trigger_Pct"] = round((after["High"].max() / entry - 1) * 100, 3)
    record["MAE_After_Trigger_Pct"] = round((after["Low"].min() / entry - 1) * 100, 3)

    risk = entry - stop
    for _, bar in after.iterrows():
        hit_stop = bool(bar["Low"] <= stop)
        hit_target = bool(bar["High"] >= target1)
        if hit_stop:
            record.update({
                "Outcome": "STOP", "Exit_Date": bar["Date"].date().isoformat(),
                "Realized_R": -1.0, "Realized_Return_Pct": round((stop / entry - 1) * 100, 3),
                "Stop_Hit": True, "Target1_Hit": hit_target,
            })
            return record
        if hit_target:
            rr = (target1 - entry) / risk
            record.update({
                "Outcome": "TARGET1", "Exit_Date": bar["Date"].date().isoformat(),
                "Realized_R": round(rr, 3), "Realized_Return_Pct": round((target1 / entry - 1) * 100, 3),
                "Target1_Hit": True,
            })
            return record

    last_close = float(after.iloc[-1]["Close"])
    record.update({
        "Outcome": "OPEN", "Exit_Date": after.iloc[-1]["Date"].date().isoformat(),
        "Realized_R": round((last_close - entry) / risk, 3),
        "Realized_Return_Pct": round((last_close / entry - 1) * 100, 3),
    })
    return record


def signal_excursion(signal_date: str, symbol: str, reference: float) -> dict[str, Any]:
    df = prices(symbol)
    future = df[df["Date"] > pd.Timestamp(signal_date)].head(5) if not df.empty else pd.DataFrame()
    if future.empty or not math.isfinite(reference) or reference <= 0:
        return {"Available_Forward_Days": len(future), "MFE_From_Signal_Pct": math.nan, "MAE_From_Signal_Pct": math.nan}
    return {
        "Available_Forward_Days": len(future),
        "MFE_From_Signal_Pct": round((future["High"].max() / reference - 1) * 100, 3),
        "MAE_From_Signal_Pct": round((future["Low"].min() / reference - 1) * 100, 3),
    }


def summarize(bt: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for model, group in bt.groupby("Model"):
        valid = group[group["Available_Forward_Days"] > 0]
        triggered = valid[valid["Triggered"] == True]
        rows.append({
            "Model": model,
            "Actionable_Signals_Total": len(group),
            "Actionable_Signals_Evaluable": len(valid),
            "Triggered": len(triggered),
            "Trigger_Rate_Pct": round(100 * len(triggered) / len(valid), 2) if len(valid) else math.nan,
            "Target1_Hit": int((triggered["Outcome"] == "TARGET1").sum()),
            "Stop_Hit": int((triggered["Outcome"] == "STOP").sum()),
            "Open_At_Data_End": int((triggered["Outcome"] == "OPEN").sum()),
            "Not_Triggered": int((valid["Outcome"] == "NOT_TRIGGERED").sum()),
            "Average_Realized_R": round(triggered["Realized_R"].mean(), 3) if len(triggered) else math.nan,
            "Median_Trigger_Delay": round(triggered["Trigger_Delay_Sessions"].median(), 2) if len(triggered) else math.nan,
        })
    return rows


def main() -> int:
    baseline = pd.read_csv(AUDIT / "decision_before_after.csv", low_memory=False)
    base_action = baseline[baseline["Baseline_Decision"].isin(["STRONG BUY", "BUY", "BUY CANDIDATE"])].copy()
    records: list[dict[str, Any]] = []
    for _, r in base_action.iterrows():
        records.append(evaluate_plan(
            "BASELINE_1.6.1", str(r["Date"]), str(r["Symbol"]), str(r["Baseline_Decision"]),
            f(r.get("Baseline_Entry")), f(r.get("Baseline_Stop")), f(r.get("Baseline_Target1")),
            str(r.get("Setup_Type_Baseline", "")),
        ))

    patched = pd.read_csv(AUDIT / "runtime_by_date/all_entry_plans.csv", low_memory=False)
    patched = patched[patched["Decision_Status_Final"].isin(["BUY READY", "BUY ON TRIGGER"])].copy()
    for _, r in patched.iterrows():
        records.append(evaluate_plan(
            "PATCHED_1.6.2", str(r["Audit_Date"]), str(r["Symbol"]), str(r["Decision_Status_Final"]),
            f(r.get("Entry_Reference_Price")), f(r.get("Initial_Stop")), f(r.get("Target_1")),
            str(r.get("Setup_Type", "")),
        ))

    bt = pd.DataFrame(records)
    bt.to_csv(AUDIT / "trigger_backtest_details.csv", index=False)
    summary = pd.DataFrame(summarize(bt))
    summary.to_csv(AUDIT / "trigger_backtest_summary.csv", index=False)

    # False-negative opportunity comparison uses actual status outputs and the same
    # audit-only excursion definition: MFE >=3% and MAE >-5% in observable sessions.
    baseline_rows = baseline.copy()
    patched_dec = pd.read_csv(AUDIT / "runtime_by_date/all_decisions.csv", low_memory=False)
    close_map = {
        (str(r["Audit_Date"]), str(r["Symbol"])): f(r.get("Close"))
        for _, r in patched_dec.iterrows()
    }
    comparisons = []
    for model, frame, date_col, status_col in [
        ("BASELINE_1.6.1", baseline_rows, "Date", "Baseline_Decision"),
        ("PATCHED_1.6.2", patched_dec, "Audit_Date", "Decision_Status"),
    ]:
        for _, r in frame.iterrows():
            # Opportunity classification must use the same signal-close reference
            # for both models; otherwise different planned entries change the label.
            ref = close_map.get((str(r[date_col]), str(r["Symbol"])), math.nan)
            exc = signal_excursion(str(r[date_col]), str(r["Symbol"]), ref)
            status = str(r[status_col])
            actionable = status in ({"STRONG BUY", "BUY", "BUY CANDIDATE"} if model.startswith("BASELINE") else {"BUY READY", "BUY ON TRIGGER"})
            opportunity = exc["Available_Forward_Days"] > 0 and exc["MFE_From_Signal_Pct"] >= 3 and exc["MAE_From_Signal_Pct"] > -5
            comparisons.append({
                "Model": model, "Signal_Date": str(r[date_col]), "Symbol": str(r["Symbol"]),
                "Decision": status, "Actionable": actionable, "Audit_Opportunity": opportunity, **exc,
            })
    cmp = pd.DataFrame(comparisons)
    cmp.to_csv(AUDIT / "opportunity_capture_details.csv", index=False)
    capture = []
    for model, group in cmp.groupby("Model"):
        valid = group[group["Available_Forward_Days"] > 0]
        opp = valid[valid["Audit_Opportunity"] == True]
        captured = opp[opp["Actionable"] == True]
        capture.append({
            "Model": model, "Evaluable_Rows": len(valid), "Audit_Opportunities": len(opp),
            "Opportunities_Captured": len(captured), "False_Negative_Candidates": len(opp) - len(captured),
            "Opportunity_Capture_Rate_Pct": round(100 * len(captured) / len(opp), 2) if len(opp) else math.nan,
            "Actionable_Rows": int(valid["Actionable"].sum()),
        })
    capture_df = pd.DataFrame(capture)
    capture_df.to_csv(AUDIT / "opportunity_capture_summary.csv", index=False)

    assumptions = {
        "signal_timing": "after signal-date close; entries only on later sessions",
        "trigger": "Entry_Reference_Price must be inside daily Low-High; zone intersection alone is insufficient",
        "same_bar_order": "stop is assumed before target when both occur in the same daily bar",
        "maximum_requested_horizon": 5,
        "maximum_available_horizon": int(bt["Available_Forward_Days"].max() if len(bt) else 0),
        "data_end": "2026-07-31",
        "opportunity_definition": "MFE >= 3% and MAE > -5% in available 1-5 sessions; audit heuristic, not strategy truth",
    }
    (AUDIT / "backtest_assumptions.json").write_text(json.dumps(assumptions, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))
    print("\nOpportunity capture:")
    print(capture_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
