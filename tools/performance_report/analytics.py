from __future__ import annotations

import math
from typing import Any

import pandas as pd


def read_csv(path):
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return default if math.isnan(value) else value
    except (TypeError, ValueError):
        return default


def metric(row: pd.Series, *names: str, default: Any = 0) -> Any:
    for name in names:
        if name in row.index and pd.notna(row[name]):
            return row[name]
    return default


def profit_factor(values: pd.Series) -> float | None:
    values = pd.to_numeric(values, errors="coerce").dropna()
    loss = float(values[values < 0].sum())
    return None if loss == 0 else float(values[values > 0].sum()) / abs(loss)


def win_rate(win: int, loss: int) -> float | None:
    return 100.0 * win / (win + loss) if win + loss else None


def raw_closed(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty or "current_status" not in ledger.columns:
        return pd.DataFrame()
    out = ledger[ledger["current_status"].astype(str).str.upper().eq("CLOSED")].copy()
    if "realized_return_pct" in out.columns:
        out["realized_return_pct"] = pd.to_numeric(out["realized_return_pct"], errors="coerce")
    return out


def setup_status(row: pd.Series) -> str:
    closed = int(num(metric(row, "Closed", "Closed_Outcomes")))
    wr = num(metric(row, "Win_Rate_Pct"))
    avg = num(metric(row, "Average_Return_Pct", "Avg_Return_Pct"))
    pf = num(metric(row, "Profit_Factor"))
    if closed < 5:
        return "LOW SAMPLE"
    if avg < 0 or (pf and pf < 1) or wr < 40:
        return "WEAK"
    if closed < 15:
        return "PROMISING"
    if wr >= 60 and avg > 0 and pf >= 1.5:
        return "STRONG"
    return "WATCH"


def setup_frames(source: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if source.empty:
        return source, pd.DataFrame()
    full = source.copy()
    full.insert(1, "Report_Status", full.apply(setup_status, axis=1))
    mapping = [
        ("Group", "Setup"), ("Report_Status", "Status"), ("Signals", "Signals"),
        ("Triggered", "Triggered"), ("Trigger_Rate_Pct", "Trigger_Rate_Pct"),
        ("Closed", "Closed"), ("Win", "Win"), ("Loss", "Loss"),
        ("Ambiguous", "Ambiguous"), ("Win_Rate_Pct", "Win_Rate_Pct"),
        ("Average_Return_Pct", "Average_Return_Pct"), ("Median_Return_Pct", "Median_Return_Pct"),
        ("Profit_Factor", "Profit_Factor"), ("Expectancy_Pct", "Expectancy_Pct"),
        ("Average_MFE_Pct", "Average_MFE_Pct"), ("Average_MAE_Pct", "Average_MAE_Pct"),
        ("Average_Holding_Days", "Average_Holding_Days"),
    ]
    cols = [src for src, _ in mapping if src in full.columns]
    snap = full[cols].rename(columns={src: dst for src, dst in mapping if src in cols})
    return full, snap


def broker_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    wanted = ["Group", "Signals", "Triggered", "Trigger_Rate_Pct", "Closed", "Win", "Loss",
              "Ambiguous", "Win_Rate_Pct", "Average_Return_Pct", "Profit_Factor", "Expectancy_Pct",
              "Average_MFE_Pct", "Average_MAE_Pct"]
    return frame[[c for c in wanted if c in frame.columns]].copy() if not frame.empty else pd.DataFrame()


def score_analysis(ledger: pd.DataFrame) -> pd.DataFrame:
    closed = raw_closed(ledger)
    columns = ["Score_Bucket", "Closed_Raw", "Win", "Loss", "Ambiguous", "Win_Rate_Pct",
               "Average_Return_Pct", "Profit_Factor", "Sample_Status"]
    if closed.empty or not {"score", "final_outcome", "realized_return_pct"}.issubset(closed.columns):
        return pd.DataFrame(columns=columns)
    closed["score"] = pd.to_numeric(closed["score"], errors="coerce")
    closed = closed.dropna(subset=["score"])
    labels = ["<60", "60-64", "65-69", "70-74", "75-79", "80-84", "85+"]
    closed["bucket"] = pd.cut(closed["score"], [-math.inf, 60, 65, 70, 75, 80, 85, math.inf], labels=labels, right=False)
    rows = []
    for label in labels:
        part = closed[closed["bucket"].eq(label)]
        if part.empty:
            continue
        outcomes = part["final_outcome"].astype(str).str.upper()
        win, loss = int(outcomes.eq("WIN").sum()), int(outcomes.eq("LOSS").sum())
        ret = pd.to_numeric(part["realized_return_pct"], errors="coerce")
        rows.append({"Score_Bucket": label, "Closed_Raw": len(part), "Win": win, "Loss": loss,
                     "Ambiguous": int(outcomes.eq("AMBIGUOUS").sum()), "Win_Rate_Pct": win_rate(win, loss),
                     "Average_Return_Pct": float(ret.mean()) if ret.notna().any() else None,
                     "Profit_Factor": profit_factor(ret), "Sample_Status": "LOW SAMPLE" if len(part) < 10 else "USABLE"})
    return pd.DataFrame(rows, columns=columns)


def time_analysis(ledger: pd.DataFrame) -> pd.DataFrame:
    closed = raw_closed(ledger)
    if closed.empty or "realized_return_pct" not in closed.columns:
        return pd.DataFrame()
    date_col = "exit_date" if "exit_date" in closed.columns else "signal_date"
    if date_col not in closed.columns:
        return pd.DataFrame()
    closed["_date"] = pd.to_datetime(closed[date_col], errors="coerce")
    closed = closed.dropna(subset=["_date"])
    if closed.empty:
        return pd.DataFrame()
    closed["Month"] = closed["_date"].dt.to_period("M").astype(str)
    rows = []
    for month, part in closed.groupby("Month", sort=True):
        outcomes = part.get("final_outcome", pd.Series(index=part.index, dtype=str)).astype(str).str.upper()
        win, loss = int(outcomes.eq("WIN").sum()), int(outcomes.eq("LOSS").sum())
        ret = pd.to_numeric(part["realized_return_pct"], errors="coerce")
        rows.append({"Month": month, "Closed_Raw": len(part), "Win": win, "Loss": loss,
                     "Win_Rate_Pct": win_rate(win, loss), "Average_Return_Pct": float(ret.mean()) if ret.notna().any() else None,
                     "Profit_Factor": profit_factor(ret)})
    return pd.DataFrame(rows)


def equity_curve(ledger: pd.DataFrame) -> pd.DataFrame:
    closed = raw_closed(ledger)
    if closed.empty or "realized_return_pct" not in closed.columns:
        return pd.DataFrame()
    date_col = "exit_date" if "exit_date" in closed.columns else "signal_date"
    if date_col not in closed.columns:
        return pd.DataFrame()
    closed["_date"] = pd.to_datetime(closed[date_col], errors="coerce")
    closed["_ret"] = pd.to_numeric(closed["realized_return_pct"], errors="coerce")
    closed = closed.dropna(subset=["_date", "_ret"]).sort_values(["_date"] + (["signal_id"] if "signal_id" in closed.columns else []))
    idx, rows = 100.0, []
    for _, row in closed.iterrows():
        idx *= 1 + float(row["_ret"]) / 100
        rows.append({"Exit_Date": row["_date"].date().isoformat(), "Signal_ID": row.get("signal_id", ""),
                     "Symbol": row.get("symbol", ""), "Setup": row.get("setup_type", ""),
                     "Return_Pct": float(row["_ret"]), "Exploratory_Index": idx})
    return pd.DataFrame(rows)


def generated_frames(summary, ledger, portfolio, integrity, exit_summary, setup_snap, broker_snap):
    row = summary.iloc[0] if not summary.empty else pd.Series(dtype=object)
    raw_count = len(raw_closed(ledger))
    canonical = int(num(metric(row, "Closed", "Closed_Outcomes")))
    excluded = int(num(metric(row, "Canonical_Excluded_Episodes", default=max(raw_count - canonical, 0))))
    universe = pd.DataFrame([
        {"Universe": "Canonical performance", "Closed": canonical, "Purpose": "Primary FTJ KPI; source of truth from PERFORMANCE_SUMMARY.csv"},
        {"Universe": "Raw closed ledger", "Closed": raw_count, "Purpose": "Exploratory breakdowns only; may include excluded/integrity episodes"},
        {"Universe": "Canonical excluded episodes", "Closed": excluded, "Purpose": "Reported by canonical tracker; not tradable performance"},
    ])
    actual = portfolio[portfolio["current_status"].astype(str).str.upper().eq("CLOSED")].copy() if not portfolio.empty and "current_status" in portfolio.columns else pd.DataFrame()
    actual_ret = pd.to_numeric(actual.get("realized_return_pct", pd.Series(dtype=float)), errors="coerce").dropna()
    aw, al = int((actual_ret > 0).sum()), int((actual_ret < 0).sum())
    comparison = pd.DataFrame([
        {"Universe": "Canonical Model Performance", "Closed": canonical, "Win": int(num(metric(row, "Win"))), "Loss": int(num(metric(row, "Loss"))),
         "Win_Rate_Pct": num(metric(row, "Win_Rate_Pct")), "Average_Return_Pct": num(metric(row, "Average_Return_Pct")),
         "Profit_Factor": num(metric(row, "Profit_Factor")), "Interpretation": "Primary SDE model performance"},
        {"Universe": "Actual Portfolio Records", "Closed": len(actual), "Win": aw, "Loss": al, "Win_Rate_Pct": win_rate(aw, al),
         "Average_Return_Pct": float(actual_ret.mean()) if len(actual_ret) else None, "Profit_Factor": profit_factor(actual_ret) if len(actual_ret) else None,
         "Interpretation": "Execution/manual records; not assumed comparable to model universe"},
    ])
    exit_row = exit_summary.iloc[0] if not exit_summary.empty else pd.Series(dtype=object)
    quality = pd.DataFrame([
        {"Check": "Raw closed ledger", "Value": raw_count, "Status": "INFO"},
        {"Check": "Canonical closed", "Value": canonical, "Status": "SOURCE OF TRUTH"},
        {"Check": "Canonical excluded episodes", "Value": excluded, "Status": "REVIEW" if excluded else "OK"},
        {"Check": "Integrity flags", "Value": len(integrity), "Status": "REVIEW" if len(integrity) else "OK"},
        {"Check": "Exit geometry invalid", "Value": int(num(metric(exit_row, "Geometry_Invalid"))), "Status": "REVIEW" if num(metric(exit_row, "Geometry_Invalid")) else "OK"},
        {"Check": "Outcome inconsistencies", "Value": int(num(metric(exit_row, "Outcome_Inconsistencies"))), "Status": "REVIEW" if num(metric(exit_row, "Outcome_Inconsistencies")) else "OK"},
        {"Check": "Replay fidelity", "Value": str(metric(row, "Replay_Fidelity", default="UNKNOWN")), "Status": "INFO"},
        {"Check": "Full live replay", "Value": str(metric(row, "Full_Live_Replay", default="UNKNOWN")), "Status": "INFO"},
    ])
    overview = pd.DataFrame(columns=["Metric", "Value"]) if summary.empty else pd.DataFrame([{"Metric": c, "Value": row[c]} for c in summary.columns])
    return {"Overview": overview, "Universe Summary": universe, "Setup Snapshot": setup_snap, "Score Analysis": score_analysis(ledger),
            "Broker Snapshot": broker_snap, "Time Analysis": time_analysis(ledger), "Equity Curve": equity_curve(ledger),
            "Model vs Portfolio": comparison, "Data Quality": quality}
