#!/usr/bin/env python3
"""Run moderate decision profiles on identical facts without changing production."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.decision_engine.smart_selective_v162 import smart_decision
from modules.entry_plan_validator.validator import finalize_entry_plan
from modules.runtime_config import load_runtime_config
from swing_utils import make_run_id, write_json

DEFAULT_PROFILES = ("MODERATE_BASELINE", "MODERATE_BALANCED", "MODERATE_FLEXIBLE")


def _num(value: Any, default: float = float("nan")) -> float:
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else default
    except Exception:
        return default


def _liquidity_score(row: dict[str, Any]) -> float:
    existing = _num(row.get("Liquidity_Score"))
    if math.isfinite(existing):
        return existing
    turnover = _num(row.get("Turnover_MA_20"), 0.0)
    if turnover >= 1e11:
        return 90.0
    if turnover >= 5e10:
        return 82.0
    if turnover >= 2e10:
        return 72.0
    if turnover >= 1e10:
        return 62.0
    if turnover >= 5e9:
        return 48.0
    if turnover >= 2e9:
        return 32.0
    return 12.0


def _plan_index(plans: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    if plans is None or plans.empty or "Symbol" not in plans:
        return {}
    return {
        str(row["Symbol"]).upper().replace(".JK", ""): row.to_dict()
        for _, row in plans.iterrows()
    }


def run_profiles(
    facts: pd.DataFrame,
    *,
    decision_config: dict[str, Any],
    profiles: Iterable[str] = DEFAULT_PROFILES,
    entry_plans: pd.DataFrame | None = None,
) -> pd.DataFrame:
    plan_map = _plan_index(entry_plans)
    rows: list[dict[str, Any]] = []
    for _, source in facts.iterrows():
        raw = source.to_dict()
        symbol = str(raw.get("Symbol", raw.get("EMITEN", ""))).upper().replace(".JK", "")
        liquidity_score = _liquidity_score(raw)
        liquidity_class = str(raw.get("Liquidity_Class", "UNKNOWN"))
        market_regime = str(raw.get("Market_Regime", "UNKNOWN"))
        for profile_name in profiles:
            result = smart_decision(
                raw,
                liquidity_score,
                liquidity_class,
                market_regime,
                policy=decision_config,
                profile_name=profile_name,
            )
            final_status = result["Decision_Status_Final"]
            plan = plan_map.get(symbol)
            if plan and result["Decision_Status_PrePlan"] == "BUY ON TRIGGER":
                finalization = finalize_entry_plan(
                    preplan_status=result["Decision_Status_PrePlan"],
                    plan_status=str(plan.get("Plan_Status", "CONDITIONAL")),
                    plan_reason=str(plan.get("Rejection_Reason", "")),
                    rejected_by_preplan=result["Rejected_By"],
                    decision_trace_preplan=result["Decision_Trace"],
                    major_rr=_num(plan.get("RR_To_Major_Resistance"), None),
                    risk_pct=_num(plan.get("Risk_Pct"), None),
                    trigger_confirmed=str(plan.get("Plan_Status", "")).upper() == "ACCEPT",
                    position_size_multiplier=float(result.get("Position_Size_Multiplier", 1.0)),
                )
                final_status = finalization["Decision_Status_Final"]
            rows.append({
                "Symbol": symbol,
                "Moderate_Profile": profile_name,
                "Decision_Status_PrePlan": result["Decision_Status_PrePlan"],
                "Decision_Status_Final": final_status,
                "Final_Score": result["Final_Score_V3"],
                "Setup_Type": str(raw.get("Setup_Type", "DEVELOPING")).upper(),
                "Market_Regime": market_regime.upper(),
                "Technical_Quality": _num(raw.get("Technical_Quality_Score"), _num(raw.get("Technical_Score_Final"), 0.0)),
                "Entry_Readiness": _num(raw.get("Entry_Readiness_PreScore"), 0.0),
                "Broker_Context": result["Broker_Context"],
                "Foreign_Context": result["Foreign_Context"],
                "Liquidity_Class": result["Liquidity_Execution_Class"],
                "Extension_Class": result["Extension_Class"],
                "Position_Size_Multiplier": result["Position_Size_Multiplier"],
                "Estimated_Slippage_Pct": result["Liquidity_Estimated_Slippage_Pct"],
                "Hard_Blockers": result["Hard_Blockers"],
                "Soft_Penalties": result["Soft_Penalties"],
                "Execution_Conditions": result["Execution_Conditions"],
            })
    return pd.DataFrame(rows)


def _outcome_columns(outcomes: pd.DataFrame | None) -> dict[str, str | None]:
    if outcomes is None or outcomes.empty:
        return {}
    normalized = {str(c).strip().lower(): c for c in outcomes.columns}
    def find(*names: str) -> str | None:
        return next((normalized[name.lower()] for name in names if name.lower() in normalized), None)
    return {
        "symbol": find("Symbol", "EMITEN"),
        "r": find("Return_R", "Expectancy_R", "R_Multiple", "Final_R"),
        "mfe": find("MFE_R", "MFE", "MFE_D7"),
        "mae": find("MAE_R", "MAE", "MAE_D7"),
        "outcome": find("Final_Outcome", "Outcome", "Result"),
        "slippage": find("Slippage_Pct", "Estimated_Slippage_Pct"),
        "holding": find("Holding_Days", "Hold_Days"),
        "return": find("Return_5D", "Return_D5", "Return_Pct"),
    }


def comparison_metrics(shadow: pd.DataFrame, outcomes: pd.DataFrame | None = None) -> pd.DataFrame:
    merged = shadow.copy()
    columns = _outcome_columns(outcomes)
    if columns.get("symbol"):
        work = outcomes.copy()
        work["_Symbol"] = work[columns["symbol"]].astype(str).str.upper().str.replace(".JK", "", regex=False)
        merged = merged.merge(work, how="left", left_on="Symbol", right_on="_Symbol", suffixes=("", "_Outcome"))

    records: list[dict[str, Any]] = []
    for profile, group in merged.groupby("Moderate_Profile", sort=False):
        statuses = group["Decision_Status_Final"].value_counts()
        actionable = group["Decision_Status_Final"].isin(["BUY READY", "BUY ON TRIGGER"])
        ready = int(statuses.get("BUY READY", 0))
        trigger = int(statuses.get("BUY ON TRIGGER", 0))
        record: dict[str, Any] = {
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
        trigger_observed_col = next((c for c in group.columns if str(c).lower() in {"trigger_observed", "trigger_hit", "triggered"}), None)
        if trigger_observed_col:
            eligible = group["Decision_Status_PrePlan"].eq("BUY ON TRIGGER")
            observed = group.loc[eligible, trigger_observed_col].astype(str).str.lower().isin({"1", "true", "yes", "hit"})
            record["Actual_Trigger_Rate"] = float(observed.mean()) if len(observed) else np.nan
        else:
            record["Actual_Trigger_Rate"] = np.nan
        if columns.get("r"):
            r = pd.to_numeric(group.loc[actionable, columns["r"]], errors="coerce")
            record["Win_Rate"] = float((r > 0).mean()) if r.notna().any() else np.nan
            record["Expectancy_R"] = float(r.mean()) if r.notna().any() else np.nan
            record["False_Positive"] = int((r <= 0).sum())
        else:
            record.update({"Win_Rate": np.nan, "Expectancy_R": np.nan, "False_Positive": np.nan})
        for metric, title in (("mfe", "Average_MFE"), ("mae", "Average_MAE"), ("holding", "Average_Holding_Period")):
            col = columns.get(metric)
            values = pd.to_numeric(group.loc[actionable, col], errors="coerce") if col else pd.Series(dtype=float)
            record[title] = float(values.mean()) if values.notna().any() else np.nan
        outcome_col = columns.get("outcome")
        if outcome_col:
            outcomes_text = group.loc[actionable, outcome_col].astype(str).str.upper()
            record["Stop_Rate"] = float(outcomes_text.str.contains("SL|STOP", regex=True).mean())
            record["Target_1_Rate"] = float(outcomes_text.str.contains("TP1|TARGET_1", regex=True).mean())
            record["Target_2_Rate"] = float(outcomes_text.str.contains("TP2|TARGET_2", regex=True).mean())
        else:
            record.update({"Stop_Rate": np.nan, "Target_1_Rate": np.nan, "Target_2_Rate": np.nan})
        return_col = columns.get("return")
        if return_col:
            future_return = pd.to_numeric(group[return_col], errors="coerce")
            missed = ~actionable & (future_return >= 3.0)
            record["False_Negative"] = int(missed.sum())
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run three moderate profiles in shadow mode")
    parser.add_argument("facts_csv", type=Path)
    parser.add_argument("--entry-plans", type=Path)
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/pipeline.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/output/profile_shadow"))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    run_id = args.run_id or make_run_id("SHADOW")
    cfg, provenance = load_runtime_config(args.config, strict=args.config.name.lower() == "pipeline.json")
    profiles = cfg.get("decision", {}).get("shadow_profiles", list(DEFAULT_PROFILES))
    facts = pd.read_csv(args.facts_csv, low_memory=False)
    plans = pd.read_csv(args.entry_plans, low_memory=False) if args.entry_plans and args.entry_plans.exists() and args.entry_plans.stat().st_size else None
    outcomes = pd.read_csv(args.outcomes, low_memory=False) if args.outcomes and args.outcomes.exists() and args.outcomes.stat().st_size else None
    shadow = run_profiles(facts, decision_config=cfg.get("decision", {}), profiles=profiles, entry_plans=plans)
    comparison = comparison_metrics(shadow, outcomes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    shadow_path = args.output_dir / "PROFILE_SHADOW_DETAIL.csv"
    comparison_path = args.output_dir / "PROFILE_COMPARISON.csv"
    shadow.to_csv(shadow_path, index=False, encoding="utf-8-sig")
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    manifest = {
        "Run_ID": run_id,
        "Mode": "SHADOW_ONLY",
        "Auto_Entry_Enabled": False,
        "Profiles": profiles,
        "Input_Rows": len(facts),
        "Output_Rows": len(shadow),
        "Config_Hash": provenance.get("config_hash", ""),
        "Outcomes_Available": outcomes is not None,
        "Minimum_Shadow_Sessions": cfg.get("analytics", {}).get("minimum_shadow_sessions", 20),
        "Live_Shadow_Validated": False,
        "Files": {"detail": str(shadow_path), "comparison": str(comparison_path)},
    }
    write_json(args.output_dir / "PROFILE_SHADOW_MANIFEST.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
