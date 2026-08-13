#!/usr/bin/env python3
import argparse, csv, json, math, sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import PACKAGE_VERSION, PIPELINE_VERSION, file_sha256, make_run_id, write_json
from modules.decision_engine.smart_selective_v162 import smart_decision
from modules.runtime_config import load_runtime_config


def n(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return 0.0


def score(tv, lots, today, today_lots):
    s = 0
    s += 65 if tv >= 1e11 else 57 if tv >= 5e10 else 48 if tv >= 2e10 else 38 if tv >= 1e10 else 25 if tv >= 5e9 else 13 if tv >= 2e9 else 6 if tv >= 1e9 else 0
    s += 25 if lots >= 1e6 else 22 if lots >= 5e5 else 18 if lots >= 2e5 else 14 if lots >= 1e5 else 8 if lots >= 5e4 else 4 if lots >= 2.5e4 else 0
    s += 10 if today >= 1e10 and today_lots >= 1e5 else 7 if today >= 5e9 and today_lots >= 5e4 else 4 if today >= 2e9 and today_lots >= 2.5e4 else 0
    return min(100, s)


def calculate_market_status(ihsg_path: Path | None) -> dict:
    status = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": None,
        "market_regime": "UNKNOWN",
        "ihsg_close": None,
        "ma20": None,
        "ma50": None,
        "ma20_slope": None,
        "source": str(ihsg_path) if ihsg_path else None,
        "reason": "IHSG file not supplied",
    }
    if not ihsg_path:
        return status
    if not ihsg_path.exists() or ihsg_path.stat().st_size == 0:
        status["reason"] = "IHSG file missing or empty"
        return status
    try:
        df = pd.read_csv(ihsg_path, low_memory=False)
        mapping = {str(c).strip().lower(): c for c in df.columns}
        date_col = mapping.get("date")
        close_col = mapping.get("close") or mapping.get("adj close") or mapping.get("adj_close")
        if close_col is None:
            status["reason"] = "Close column not found in IHSG file"
            return status
        work = pd.DataFrame({"Close": pd.to_numeric(df[close_col], errors="coerce")})
        if date_col is not None:
            work["Date"] = pd.to_datetime(df[date_col], errors="coerce")
            work = work.sort_values("Date")
        work = work.dropna(subset=["Close"]).reset_index(drop=True)
        if len(work) < 50:
            status["reason"] = f"Need at least 50 IHSG rows; found {len(work)}"
            return status
        work["MA20"] = work["Close"].rolling(20).mean()
        work["MA50"] = work["Close"].rolling(50).mean()
        latest = work.iloc[-1]
        prev_ma20 = work["MA20"].iloc[-6] if len(work) >= 55 else work["MA20"].iloc[-2]
        slope = float(latest["MA20"] - prev_ma20)
        close = float(latest["Close"])
        ma20 = float(latest["MA20"])
        ma50 = float(latest["MA50"])
        if close > ma20 > ma50 and slope > 0:
            regime = "BULL"
            reason = "Close > MA20 > MA50 and MA20 slope is positive"
        elif close < ma20 < ma50 and slope < 0:
            regime = "BEAR"
            reason = "Close < MA20 < MA50 and MA20 slope is negative"
        else:
            regime = "SIDEWAYS"
            reason = "Trend conditions are mixed"
        status.update({
            "date": str(latest["Date"].date()) if "Date" in latest and pd.notna(latest["Date"]) else None,
            "market_regime": regime,
            "ihsg_close": round(close, 4),
            "ma20": round(ma20, 4),
            "ma50": round(ma50, 4),
            "ma20_slope": round(slope, 4),
            "reason": reason,
        })
        return status
    except Exception as exc:
        status["reason"] = f"Failed to calculate regime: {exc}"
        return status


def _normalize_input(row: dict, liquidity_score: float) -> dict:
    """Adapt historical inputs into the canonical Stage 2 fact contract."""
    normalized = dict(row)
    quality = n(row.get("Technical_Quality_Score")) or n(row.get("Technical_Score_Final"))
    new_mode = any(str(row.get(key, "")).strip().lower() not in {"", "nan", "none"} for key in (
        "Technical_Quality_Score", "Entry_Readiness_PreScore", "Broker_Confidence", "Broker_Direction"
    ))
    normalized["Technical_Quality_Score"] = quality
    normalized["Entry_Readiness_PreScore"] = n(row.get("Entry_Readiness_PreScore")) or min(quality, 70.0)
    confirmation = str(row.get("Broker_Confirmation", "NEUTRAL") or "NEUTRAL").upper()
    direction = str(row.get("Broker_Direction", "") or "").upper()
    if not direction:
        direction = "ACCUMULATION" if "ACCUMULATION" in confirmation else "DISTRIBUTION" if "DISTRIBUTION" in confirmation else "NEUTRAL"
    normalized["Broker_Direction"] = direction
    normalized["Broker_Confidence"] = n(row.get("Broker_Confidence")) or (70.0 if direction == "ACCUMULATION" else 70.0 if direction == "DISTRIBUTION" else 50.0)
    normalized.setdefault("Relative_Rank_Pct", 50.0)
    normalized.setdefault("Foreign_Score", 50.0)
    normalized.setdefault("Foreign_Confidence", 0.0)
    normalized.setdefault("Setup_Type", "DEVELOPING")
    normalized["_Legacy_Input"] = not new_mode
    normalized["Liquidity_Score"] = liquidity_score
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="SDE Swing single Final Decision Engine V1.6.2 Stage 2")
    parser.add_argument("source", nargs="?", default="FINAL_DECISION_V2.csv")
    parser.add_argument("output", nargs="?", default="decision_v3_output")
    parser.add_argument("--ihsg", help="IHSG historical CSV with Date and Close columns")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--profile", default=None, help="Moderate profile; production default comes from config")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest-dir", default=None)
    parser.add_argument("--data-quality-status", default="VALID")
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    runtime_cfg, config_provenance = load_runtime_config(config_path, strict=config_path.name.lower() == "pipeline.json")
    decision_policy = runtime_cfg.get("decision", {})
    profile_name = args.profile or decision_policy.get("production_profile", "MODERATE_BASELINE")

    src = Path(args.source)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    market_status = calculate_market_status(Path(args.ihsg) if args.ihsg else None)
    with src.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError("Source decision CSV is empty")

    resolved_rows: list[dict] = []
    for raw in rows:
        turnover = n(raw.get("Turnover_MA_20"))
        lots = n(raw.get("Volume_MA_20")) / 100.0
        today = n(raw.get("Turnover_Value"))
        today_lots = n(raw.get("Volume")) / 100.0
        liquidity_score = score(turnover, lots, today, today_lots)
        liquidity_class = "VERY LIQUID" if turnover >= 5e10 else "LIQUID" if turnover >= 2e10 else "ADEQUATE" if turnover >= 1e10 else "THIN" if turnover >= 5e9 else "ILLIQUID"
        facts = _normalize_input(raw, liquidity_score)
        facts["Data_Quality_Status"] = args.data_quality_status
        telemetry = smart_decision(
            facts,
            liquidity_score,
            liquidity_class,
            market_status["market_regime"],
            policy=decision_policy,
            profile_name=profile_name,
        )
        quality = n(facts.get("Technical_Quality_Score"))
        readiness = n(facts.get("Entry_Readiness_PreScore"))
        result = dict(raw)
        result.update({
            "Run_ID": args.run_id,
            "Market_Regime": market_status["market_regime"],
            "Liquidity_Score": f"{liquidity_score:.2f}",
            "Liquidity_Class": liquidity_class,
            "Turnover_MA20_Billion": f"{turnover / 1e9:.3f}",
            "Volume_MA20_Lots": f"{lots:.0f}",
            "Technical_Quality_Score_Final": f"{quality:.2f}",
            "Entry_Readiness_PreScore_Final": f"{readiness:.2f}",
            "Broker_Confidence_Final": f"{n(facts.get('Broker_Confidence')):.2f}",
            "Broker_Direction_Final": facts.get("Broker_Direction", "NEUTRAL"),
            "Data_Quality_Status": args.data_quality_status,
            "Config_Source": config_provenance.get("config_source", ""),
            "Config_Hash": config_provenance.get("config_hash", ""),
            "Config_Version": config_provenance.get("config_version", ""),
        })
        result.update(telemetry)
        result["Decision_Status_Final"] = result["Decision_Status"]
        resolved_rows.append(result)

    priority = {"BUY ON TRIGGER": 0, "WATCH": 1, "AVOID": 2}
    resolved_rows.sort(key=lambda row: (priority.get(str(row.get("Decision_Status_Final", "AVOID")).upper(), 9), -n(row.get("Final_Score_V3"))))
    for index, row in enumerate(resolved_rows, 1):
        row["Rank_V3"] = index
    lead = [
        "Rank_V3", "Symbol", "Decision_Status_Final", "Decision_Status", "Decision_V3", "Decision_Owner",
        "Decision_Reason_Code", "Rejected_By", "Hard_Blockers", "Soft_Penalties", "Execution_Conditions",
        "Decision_Trace", "Moderate_Profile", "Threshold_Profile", "Market_Regime", "Final_Score_V3",
        "Technical_Quality_Score_Final", "Entry_Readiness_PreScore_Final", "Broker_Direction_Final",
        "Broker_Confidence_Final", "Liquidity_Score", "Liquidity_Class", "Liquidity_Execution_Class",
        "Extension_Class", "Position_Size_Multiplier", "Turnover_MA20_Billion", "Volume_MA20_Lots",
    ]
    fields = lead + [key for key in resolved_rows[0] if key not in set(lead)]
    decision_path = out / "FINAL_DECISION_V3.csv"
    with decision_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(resolved_rows)
    status_path = out / "MARKET_STATUS.json"
    write_json(status_path, market_status)
    manifest = {
        "Run_ID": args.run_id,
        "Pipeline_Version": PIPELINE_VERSION,
        "Engine_Version": PACKAGE_VERSION,
        "Decision_Owner": "DECISION_ENGINE",
        "Moderate_Profile": profile_name,
        "Config_Source": config_provenance.get("config_source", ""),
        "Config_Hash": config_provenance.get("config_hash", ""),
        "Config_Version": config_provenance.get("config_version", ""),
        "Input": str(src.resolve()),
        "Input_SHA256": file_sha256(src),
        "Output": str(decision_path.resolve()),
        "Rows": len(resolved_rows),
        "Status_Counts": pd.Series([row["Decision_Status_Final"] for row in resolved_rows]).value_counts().to_dict(),
        "Data_Quality_Status": args.data_quality_status,
    }
    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else out
    manifest_dir.mkdir(parents=True, exist_ok=True)
    write_json(manifest_dir / f"DECISION_ENGINE_MANIFEST_{args.run_id}.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
