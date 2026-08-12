#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

"""Lifecycle-metric facade for moderate profile shadow evaluation."""

import json
import numpy as np
import pandas as pd

from modules.analytics import profile_shadow_baseline as _baseline

for _name in dir(_baseline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_baseline, _name)


def _find_column(frame: pd.DataFrame, *names: str) -> str | None:
    normalized = {str(c).strip().lower(): c for c in frame.columns}
    return next((normalized[name.lower()] for name in names if name.lower() in normalized), None)


def _as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "hit"})


def comparison_metrics(shadow: pd.DataFrame, outcomes: pd.DataFrame | None = None) -> pd.DataFrame:
    merged = shadow.copy()
    columns = _baseline._outcome_columns(outcomes)
    explicit_hits: dict[str, str | None] = {}
    if outcomes is not None and not outcomes.empty:
        explicit_hits = {
            "tp1": _find_column(outcomes, "TP1_Hit", "tp1_hit", "TP1_Hit_D7"),
            "tp2": _find_column(outcomes, "TP2_Hit", "tp2_hit", "TP2_Hit_D7"),
            "sl": _find_column(outcomes, "SL_Hit", "sl_hit", "SL_Hit_D7"),
            "final": _find_column(outcomes, "Final_Outcome", "final_outcome", "Final_Outcome_D7"),
            "r": _find_column(outcomes, "Return_R", "return_r", "Return_R_D7"),
        }
    if columns.get("symbol"):
        work = outcomes.copy()
        work["_Symbol"] = (
            work[columns["symbol"]].astype(str).str.upper().str.replace(".JK", "", regex=False)
        )
        merged = merged.merge(work, how="left", left_on="Symbol", right_on="_Symbol", suffixes=("", "_Outcome"))

    records: list[dict] = []
    for profile, group in merged.groupby("Moderate_Profile", sort=False):
        statuses = group["Decision_Status_Final"].value_counts()
        actionable = group["Decision_Status_Final"].isin(["BUY READY", "BUY ON TRIGGER"])
        ready = int(statuses.get("BUY READY", 0))
        trigger = int(statuses.get("BUY ON TRIGGER", 0))
        record = {
            "Moderate_Profile": profile,
            "Candidates": int(len(group)),
            "BUY_READY": ready,
            "BUY_ON_TRIGGER": trigger,
            "WATCH": int(statuses.get("WATCH", 0)),
            "AVOID": int(statuses.get("AVOID", 0)),
            "Ready_Conversion_Ratio": ready / max(ready + trigger, 1),
            "Average_Score": float(group["Final_Score"].mean()),
            "Average_Estimated_Slippage_Pct": float(group.loc[actionable, "Estimated_Slippage_Pct"].mean()) if actionable.any() else np.nan,
            "Average_Position_Multiplier": float(group.loc[actionable, "Position_Size_Multiplier"].mean()) if actionable.any() else np.nan,
        }
        trigger_observed_col = next(
            (c for c in group.columns if str(c).lower() in {"trigger_observed", "trigger_hit", "triggered"}),
            None,
        )
        if trigger_observed_col:
            eligible = group["Decision_Status_PrePlan"].eq("BUY ON TRIGGER")
            observed = _as_bool(group.loc[eligible, trigger_observed_col])
            record["Actual_Trigger_Rate"] = float(observed.mean()) if len(observed) else np.nan
        else:
            record["Actual_Trigger_Rate"] = np.nan

        r_col = explicit_hits.get("r") or columns.get("r")
        if r_col and r_col in group:
            r = pd.to_numeric(group.loc[actionable, r_col], errors="coerce")
            record["Win_Rate"] = float((r > 0).mean()) if r.notna().any() else np.nan
            record["Expectancy_R"] = float(r.mean()) if r.notna().any() else np.nan
            record["False_Positive"] = int((r <= 0).sum())
        else:
            final_col = explicit_hits.get("final")
            if final_col and final_col in group:
                final = group.loc[actionable, final_col].astype(str).str.upper()
                closed = final.isin({"WIN", "LOSS", "AMBIGUOUS"})
                record["Win_Rate"] = float(final[closed].eq("WIN").mean()) if closed.any() else np.nan
                record["False_Positive"] = int(final.eq("LOSS").sum())
            else:
                record["Win_Rate"] = np.nan
                record["False_Positive"] = np.nan
            record["Expectancy_R"] = np.nan

        for metric, title in (("mfe", "Average_MFE"), ("mae", "Average_MAE"), ("holding", "Average_Holding_Period")):
            col = columns.get(metric)
            values = pd.to_numeric(group.loc[actionable, col], errors="coerce") if col and col in group else pd.Series(dtype=float)
            record[title] = float(values.mean()) if values.notna().any() else np.nan

        if explicit_hits.get("sl") and explicit_hits["sl"] in group:
            record["Stop_Rate"] = float(_as_bool(group.loc[actionable, explicit_hits["sl"]]).mean()) if actionable.any() else np.nan
        else:
            record["Stop_Rate"] = np.nan
        if explicit_hits.get("tp1") and explicit_hits["tp1"] in group:
            record["Target_1_Rate"] = float(_as_bool(group.loc[actionable, explicit_hits["tp1"]]).mean()) if actionable.any() else np.nan
        else:
            record["Target_1_Rate"] = np.nan
        if explicit_hits.get("tp2") and explicit_hits["tp2"] in group:
            record["Target_2_Rate"] = float(_as_bool(group.loc[actionable, explicit_hits["tp2"]]).mean()) if actionable.any() else np.nan
        else:
            record["Target_2_Rate"] = np.nan

        outcome_col = columns.get("outcome")
        if outcome_col and not any(explicit_hits.get(k) for k in ("sl", "tp1", "tp2")):
            text = group.loc[actionable, outcome_col].astype(str).str.upper()
            record["Stop_Rate"] = float(text.str.contains("SL|STOP", regex=True).mean())
            record["Target_1_Rate"] = float(text.str.contains("TP1|TARGET_1", regex=True).mean())
            record["Target_2_Rate"] = float(text.str.contains("TP2|TARGET_2", regex=True).mean())

        return_col = columns.get("return")
        if return_col and return_col in group:
            future_return = pd.to_numeric(group[return_col], errors="coerce")
            record["False_Negative"] = int((~actionable & (future_return >= 3.0)).sum())
        else:
            record["False_Negative"] = np.nan

        record["Result_By_Setup"] = json.dumps(
            group.groupby(["Setup_Type", "Decision_Status_Final"]).size().unstack(fill_value=0).to_dict(orient="index"),
            ensure_ascii=False,
        )
        record["Result_By_Market_Regime"] = json.dumps(
            group.groupby(["Market_Regime", "Decision_Status_Final"]).size().unstack(fill_value=0).to_dict(orient="index"),
            ensure_ascii=False,
        )
        records.append(record)
    return pd.DataFrame(records)


_baseline.comparison_metrics = comparison_metrics

if __name__ == "__main__":
    raise SystemExit(_baseline.main())
