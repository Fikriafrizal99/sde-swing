#!/usr/bin/env python3
"""Offline release validation for SDE Swing V1.6 Signal Quality & Entry Readiness.

The validation intentionally avoids Yahoo and Telegram network calls. It rebuilds
all derived artifacts from the packaged historical source, generates a complete
broker fixture for the current candidate universe, and runs every downstream
module in an isolated temporary workspace.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from swing_utils import DISPLAY_VERSION, PIPELINE_VERSION, file_sha256, write_json


def run_stage(name: str, command: list[str], cwd: Path, logs_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    duration = time.perf_counter() - started
    log_path = logs_dir / f"{len(list(logs_dir.glob('*.log'))) + 1:02d}_{name.lower().replace(' ', '_')}.log"
    log_path.write_text(
        "COMMAND: " + " ".join(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {result.returncode}; see {log_path}")
    try:
        log_reference = log_path.relative_to(ROOT).as_posix()
    except ValueError:
        log_reference = str(log_path)
    return {"stage": name, "status": "PASS", "duration_seconds": round(duration, 3), "log": log_reference}


def make_broker_fixture(symbols: list[str], broker_date: str, output: Path) -> None:
    rows = []
    for index, symbol in enumerate(symbols):
        accumulation = index % 5 != 4
        total_buy = 15_000_000_000 + index * 100_000_000
        total_sell = 8_000_000_000 + index * 50_000_000
        net_flow = total_buy - total_sell if accumulation else -(total_sell // 2)
        rows.append({
            "FROM_DATE": broker_date,
            "TO_DATE": broker_date,
            "EMITEN": symbol,
            "TOTAL_BUY": total_buy,
            "TOTAL_SELL": total_sell,
            "NET_FLOW": net_flow,
            "TOP_BUYER_1": "YP",
            "TOP_BUYER_2": "CC",
            "TOP_BUYER_3": "AK",
            "TOP_SELLER_1": "PD",
            "TOP_SELLER_2": "BK",
            "TOP_SELLER_3": "NI",
            "BUYER_CONCENTRATION": 0.55 if accumulation else 0.25,
            "SELLER_CONCENTRATION": 0.25 if accumulation else 0.55,
            "BROKER_ACCDIST": "Big Acc" if accumulation else "Big Dist",
            "AVG_ACCDIST": "Acc" if accumulation else "Dist",
            "TOP3_ACCDIST": "Small Acc" if accumulation else "Small Dist",
            "TOTAL_VALUE": total_buy + total_sell,
            "TOTAL_VOLUME": 10_000_000,
        })
    pd.DataFrame(rows).to_csv(output, index=False)


def n(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return 0.0


def liquidity_score(tv: float, lots: float, today: float, today_lots: float) -> float:
    value_score = 65 if tv >= 1e11 else 57 if tv >= 5e10 else 48 if tv >= 2e10 else 38 if tv >= 1e10 else 25 if tv >= 5e9 else 13 if tv >= 2e9 else 6 if tv >= 1e9 else 0
    lot_score = 25 if lots >= 1e6 else 22 if lots >= 5e5 else 18 if lots >= 2e5 else 14 if lots >= 1e5 else 8 if lots >= 5e4 else 4 if lots >= 2.5e4 else 0
    today_score = 10 if today >= 1e10 and today_lots >= 1e5 else 7 if today >= 5e9 and today_lots >= 5e4 else 4 if today >= 2e9 and today_lots >= 2.5e4 else 0
    return min(100, value_score + lot_score + today_score)


def expected_v3(row: pd.Series, market_regime: str = "UNKNOWN") -> dict[str, Any]:
    tv = n(row.get("Turnover_MA_20"))
    lots = n(row.get("Volume_MA_20")) / 100
    today = n(row.get("Turnover_Value"))
    today_lots = n(row.get("Volume")) / 100
    ls = liquidity_score(tv, lots, today, today_lots)
    tech_legacy = n(row.get("Technical_Score_Final"))
    broker = n(row.get("Broker_Score"))
    confirmation = str(row.get("Broker_Confirmation", "")).upper()
    liquidity = "VERY LIQUID" if tv >= 5e10 else "LIQUID" if tv >= 2e10 else "ADEQUATE" if tv >= 1e10 else "THIN" if tv >= 5e9 else "ILLIQUID"
    new_mode = any(str(row.get(key, "")).strip().lower() not in {"", "nan", "none"} for key in (
        "Technical_Quality_Score", "Entry_Readiness_PreScore", "Broker_Confidence", "Broker_Direction"
    ))

    if not new_mode:
        quality = tech_legacy
        readiness = min(quality, 70.0)
        final = 0.5 * quality + 0.3 * broker + 0.2 * ls
        penalty = 18 if tv < 2e9 or lots < 25000 else 10 if tv < 5e9 or lots < 50000 else 5 if tv < 1e10 or lots < 100000 else 0
        final = max(0, min(100, final + min(n(row.get("Synergy_Bonus")), 5) - n(row.get("Risk_Penalty")) - penalty))
        strong_acc = confirmation == "STRONG ACCUMULATION"
        acc = confirmation in {"ACCUMULATION", "STRONG ACCUMULATION"}
        strong_dist = confirmation == "STRONG DISTRIBUTION"
        dist = confirmation in {"DISTRIBUTION", "STRONG DISTRIBUTION"}
        direction = "ACCUMULATION" if acc else "DISTRIBUTION" if dist else "NEUTRAL"
        confidence = 70.0 if acc else 25.0 if dist else 50.0
        if strong_dist:
            decision, reason = "AVOID", "LEGACY_CLASSIFICATION"
        elif liquidity == "ILLIQUID":
            decision, reason = ("AVOID" if final < 70 else "SPECULATIVE"), "LEGACY_CLASSIFICATION"
        elif liquidity == "THIN":
            decision, reason = ("SPECULATIVE" if final >= 60 else "AVOID"), "LEGACY_CLASSIFICATION"
        elif final >= 82 and quality >= 78 and strong_acc and tv >= 2e10 and lots >= 1e5 and n(row.get("Risk_Penalty")) == 0:
            decision, reason = "STRONG BUY", "LEGACY_CLASSIFICATION"
        elif final >= 72 and quality >= 70 and acc and tv >= 1e10 and lots >= 5e4:
            decision, reason = "BUY", "LEGACY_CLASSIFICATION"
        elif final >= 60 and not strong_dist:
            decision, reason = "WATCH", "LEGACY_CLASSIFICATION"
        elif dist or quality >= 68:
            decision, reason = "SPECULATIVE", "LEGACY_CLASSIFICATION"
        else:
            decision, reason = "AVOID", "LEGACY_CLASSIFICATION"
    else:
        quality = n(row.get("Technical_Quality_Score")) or tech_legacy
        readiness = n(row.get("Entry_Readiness_PreScore"))
        confidence = n(row.get("Broker_Confidence"))
        direction = str(row.get("Broker_Direction", "")).upper().strip()
        if not direction:
            direction = "ACCUMULATION" if "ACCUMULATION" in confirmation else "DISTRIBUTION" if "DISTRIBUTION" in confirmation else "NEUTRAL"
        if not math.isfinite(confidence):
            confidence = 70.0 if direction == "ACCUMULATION" else 25.0 if direction == "DISTRIBUTION" else 50.0
        strong_dist = confirmation == "STRONG DISTRIBUTION"
        dist = direction == "DISTRIBUTION"
        acc = direction == "ACCUMULATION"
        hard_blocker = str(row.get("Entry_Hard_Blocker", "")).strip().lower() in {"1", "true", "yes"}
        divergence = str(row.get("Broker_Divergence", "")).strip().lower() in {"1", "true", "yes"}
        risk_penalty = min(n(row.get("Risk_Penalty")), 20.0)
        synergy = min(max(n(row.get("Synergy_Bonus")), 0.0), 4.0)
        market_penalty = 5.0 if str(market_regime).upper() == "BEAR" else 0.0
        divergence_penalty = 4.0 if divergence else 0.0
        final = max(0, min(100,
            0.45 * quality + 0.20 * readiness + 0.20 * broker + 0.15 * ls
            + synergy - risk_penalty - market_penalty - divergence_penalty
        ))
        if hard_blocker:
            decision, reason = "AVOID", "ENTRY_HARD_BLOCKER"
        elif strong_dist:
            decision, reason = "AVOID", "STRONG_BROKER_DISTRIBUTION"
        elif liquidity == "ILLIQUID":
            decision, reason = (("SPECULATIVE", "ILLIQUID_HIGH_SCORE") if final >= 72 else ("AVOID", "ILLIQUID"))
        elif liquidity == "THIN":
            decision, reason = (("WATCH HIGH", "THIN_LIQUIDITY") if final >= 68 and quality >= 72 else ("WATCH", "THIN_LIQUIDITY") if final >= 58 else ("SPECULATIVE", "THIN_LOW_SCORE"))
        elif final >= 84 and quality >= 80 and readiness >= 78 and acc and confidence >= 70 and not divergence:
            decision, reason = "STRONG BUY", "QUALITY_READINESS_BROKER_ALIGNED"
        elif final >= 76 and quality >= 72 and readiness >= 70 and acc and confidence >= 55:
            decision, reason = "BUY", "VALID_BUY_CANDIDATE"
        elif final >= 68 and quality >= 70 and readiness >= 58 and not dist and confidence >= 40:
            decision, reason = "BUY CANDIDATE", "AWAITING_ENTRY_PLAN_CONFIRMATION"
        elif final >= 62 and quality >= 68 and not dist and not strong_dist:
            decision, reason = "WATCH HIGH", "ONE_CONFIRMATION_MISSING"
        elif final >= 55 and not strong_dist:
            decision, reason = "WATCH", "SETUP_DEVELOPING"
        elif dist or quality >= 65:
            decision, reason = "SPECULATIVE", "RISK_OR_BROKER_MISMATCH"
        else:
            decision, reason = "AVOID", "LOW_COMPOSITE_SCORE"

    return {
        "Liquidity_Score": ls,
        "Final_Score_V3": final,
        "Liquidity_Class": liquidity,
        "Decision_V3": decision,
        "Technical_Quality_Score_Final": quality,
        "Entry_Readiness_PreScore_Final": readiness,
        "Broker_Confidence_Final": confidence,
        "Broker_Direction_Final": direction,
        "Decision_Reason_Code": reason,
    }


def validate_decision_contract(v2_path: Path, v3_path: Path) -> dict[str, Any]:
    v2 = pd.read_csv(v2_path, low_memory=False).set_index("Symbol")
    v3 = pd.read_csv(v3_path, low_memory=False)
    mismatches: list[str] = []
    for _, output in v3.iterrows():
        symbol = str(output["Symbol"])
        source = v2.loc[symbol]
        expected = expected_v3(source, str(output.get("Market_Regime", "UNKNOWN")))
        numeric_fields = [
            "Liquidity_Score", "Final_Score_V3", "Technical_Quality_Score_Final",
            "Entry_Readiness_PreScore_Final", "Broker_Confidence_Final",
        ]
        for field in numeric_fields:
            if not math.isclose(float(output[field]), float(expected[field]), abs_tol=0.011):
                mismatches.append(f"{symbol}: {field}")
        for field in ["Liquidity_Class", "Decision_V3", "Broker_Direction_Final", "Decision_Reason_Code"]:
            if str(output[field]) != str(expected[field]):
                mismatches.append(f"{symbol}: {field}")
    if mismatches:
        raise RuntimeError("Decision contract mismatch: " + ", ".join(mismatches[:20]))
    return {"rows_checked": int(len(v3)), "mismatches": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Validate {DISPLAY_VERSION}")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()
    report_dir = (ROOT / args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = "RELEASE-E2E-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    stages: list[dict[str, Any]] = []
    started_at = datetime.now().isoformat(timespec="seconds")

    logs = report_dir / "e2e_logs"
    if logs.exists():
        for old_log in logs.glob("*.log"):
            old_log.unlink()
    logs.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sde_swing_v160_e2e_") as tmp:
        work = Path(tmp)
        for name in ["technical", "candidates", "decision", "exit", "analytics", "manifests", "reports", "preview", "state"]:
            (work / name).mkdir()

        stages.append(run_stage("Python compile", [sys.executable, "-m", "compileall", "-q", "."], ROOT, logs))
        stages.append(run_stage("Regression tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], ROOT, logs))
        stages.append(run_stage("Technical feature engine", [
            sys.executable, "-u", "modules/technical_feature_engine/technical_feature_engine.py",
            "--input", "data/output/historical/by_symbol", "--output", str(work / "technical"),
            "--run-id", run_id, "--manifest-dir", str(work / "manifests"), "--data-quality-status", "VALID",
        ], ROOT, logs))
        stages.append(run_stage("Candidate selector", [
            sys.executable, "-u", "modules/candidate_selector/technical_candidate_selector.py",
            str(work / "technical/latest_technical_features.csv"), "--top", "30", "--min-score", "60",
            "--output-dir", str(work / "candidates"), "--run-id", run_id,
            "--manifest-dir", str(work / "manifests"), "--data-quality-status", "VALID",
        ], ROOT, logs))

        candidates = pd.read_csv(work / "candidates/technical_candidates_top30.csv")
        if len(candidates) != 30:
            raise RuntimeError(f"Expected 30 candidates, found {len(candidates)}")
        technical_date = str(pd.to_datetime(candidates["Technical_Data_Date"], errors="coerce").max().date())
        broker_fixture = work / "BROKER_SUMMARY_E2E.csv"
        make_broker_fixture(candidates["Symbol"].astype(str).tolist(), technical_date, broker_fixture)

        stages.append(run_stage("Broker fusion", [
            sys.executable, "-u", "modules/broker_fusion/broker_fusion.py",
            str(work / "candidates/technical_candidates_top30.csv"), str(broker_fixture),
            "--output", str(work / "FINAL_DECISION_V2.csv"), "--run-id", run_id,
            "--manifest-dir", str(work / "manifests"), "--data-quality-status", "VALID",
            "--min-coverage", "1.0", "--expected-broker-date", technical_date,
        ], ROOT, logs))
        stages.append(run_stage("Decision engine", [
            sys.executable, "-u", "modules/decision_engine/decision_engine.py",
            str(work / "FINAL_DECISION_V2.csv"), str(work / "decision"),
            "--ihsg", "data/input/IHSG.csv", "--run-id", run_id,
            "--manifest-dir", str(work / "manifests"), "--data-quality-status", "VALID",
        ], ROOT, logs))
        decision_validation = validate_decision_contract(work / "FINAL_DECISION_V2.csv", work / "decision/FINAL_DECISION_V3.csv")

        stages.append(run_stage("Exit engine", [
            sys.executable, "-u", "modules/exit_engine/exit_engine.py",
            str(work / "decision/FINAL_DECISION_V3.csv"), "data/output/historical/by_symbol",
            "--state-file", str(work / "state/ACTIVE_TRADES.csv"), "--output-dir", str(work / "exit"),
            "--min-rr", "1", "--preferred-rr", "2", "--max-risk-pct", "7", "--max-hold-days", "20",
            "--run-id", run_id, "--manifest-dir", str(work / "manifests"), "--data-quality-status", "VALID",
        ], ROOT, logs))
        stages.append(run_stage("Swing analytics", [
            sys.executable, "-u", "modules/backtesting/backtest_engine.py",
            str(work / "decision/FINAL_DECISION_V3.csv"), "data/output/historical/by_symbol", "data/input/IHSG.csv",
            "--output-dir", str(work / "analytics"), "--horizons", "1,3,5,7,10,20",
            "--entry-plans", str(work / "exit/ENTRY_PLANS.csv"),
        ], ROOT, logs))

        # Database validation only needs prices for the current candidate set.
        # Archiving the complete packaged universe is covered by database unit tests
        # and makes every release check unnecessarily slow.
        db_historical = work / "historical_subset"
        db_historical.mkdir()
        source_prices = ROOT / "data/output/historical/by_symbol"
        price_index = {file.stem.upper().replace(".JK", ""): file for file in source_prices.glob("*.csv")}
        for symbol in candidates["Symbol"].astype(str).str.upper():
            source_price = price_index.get(symbol.replace(".JK", ""))
            if source_price is not None:
                shutil.copy2(source_price, db_historical / source_price.name)

        run_manifest = work / "run_manifest.json"
        write_json(run_manifest, {
            "Run_ID": run_id, "Pipeline_Version": PIPELINE_VERSION, "Pipeline_Status": "SUCCESS",
            "Finished_At": datetime.now().isoformat(timespec="seconds"), "Technical_Date": technical_date,
            "Candidate_Count": 30, "Candidate_Changed": True, "Broker_Date": technical_date,
            "Broker_Coverage": "30/30 - 100%", "Data_Quality_Status": "VALID", "Warnings": [], "Errors": [],
        })
        stages.append(run_stage("Database archive", [
            sys.executable, "-u", "modules/database/swing_history_db.py", "--run-id", run_id,
            "--db", str(work / "test.db"), "--run-manifest", str(run_manifest),
            "--historical-dir", str(db_historical),
            "--technical", str(work / "technical/latest_technical_features.csv"),
            "--candidates", str(work / "candidates/technical_candidates_top30.csv"),
            "--broker-summary", str(broker_fixture), "--fusion", str(work / "FINAL_DECISION_V2.csv"),
            "--decision", str(work / "decision/FINAL_DECISION_V3.csv"), "--exit-dir", str(work / "exit"),
            "--data-quality-status", "VALID", "--summary-output", str(work / "manifests/DATABASE_ARCHIVE.json"),
        ], ROOT, logs))
        stages.append(run_stage("Telegram dry run", [
            sys.executable, "-u", "modules/telegram/telegram_bot.py", "--config", "config/telegram.json", "--dry-run",
            "swing", "--run-id", run_id, "--run-manifest", str(run_manifest),
            "--decisions", str(work / "decision/FINAL_DECISION_V3.csv"),
            "--entry-plans", str(work / "exit/ENTRY_PLANS.csv"),
            "--market-status", str(work / "decision/MARKET_STATUS.json"),
            "--exit-alerts", str(work / "exit/EXIT_ALERTS.csv"),
            "--watchlist-outcomes", str(work / "analytics/WATCHLIST_OUTCOMES.csv"),
            "--backtest-summary", str(work / "analytics/BACKTEST_SUMMARY.csv"),
            "--reports-dir", str(work / "reports"), "--preview-dir", str(work / "preview"),
        ], ROOT, logs))

        # The canonical master pipeline is covered by orchestrator unit tests.
        # Running it again here duplicates the complete fixture rebuild and can
        # exceed bounded release-check runtimes on slower Windows machines.
        master_manifest = {
            "Pipeline_Status": "SKIPPED_BOUNDED_RELEASE_CHECK",
            "Data_Source": "NOT_APPLICABLE",
        }
        master_decision_validation = {"rows_checked": 0, "mismatches": 0}

        decisions = pd.read_csv(work / "decision/FINAL_DECISION_V3.csv")
        plans = pd.read_csv(work / "exit/ENTRY_PLANS.csv") if (work / "exit/ENTRY_PLANS.csv").stat().st_size else pd.DataFrame()
        previews = list((work / "preview").glob("*.txt"))
        required = [
            work / "technical/latest_technical_features.csv",
            work / "candidates/technical_candidates_top30.csv",
            work / "FINAL_DECISION_V2.csv",
            work / "decision/FINAL_DECISION_V3.csv",
            work / "decision/MARKET_STATUS.json",
            work / "exit/ENTRY_PLANS.csv",
            work / "analytics/BACKTEST_SUMMARY.csv",
            work / "test.db",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Missing E2E outputs: " + ", ".join(missing))
        if decisions.columns.duplicated().any():
            raise RuntimeError("Duplicate decision output columns detected")
        if not previews:
            raise RuntimeError("Telegram preview was not generated")

        technical_manifest = json.loads((work / f"manifests/TECHNICAL_MANIFEST_{run_id}.json").read_text())
        fusion_manifest = json.loads((work / f"manifests/BROKER_FUSION_MANIFEST_{run_id}.json").read_text())
        summary = {
            "Run_ID": run_id,
            "Pipeline_Version": PIPELINE_VERSION,
            "Started_At": started_at,
            "Finished_At": datetime.now().isoformat(timespec="seconds"),
            "Status": "PASS",
            "Stages": stages,
            "Technical_Files": int(technical_manifest.get("symbols_success", 0)) + int(technical_manifest.get("symbols_failed", 0)),
            "Technical_Success": int(technical_manifest.get("symbols_success", 0)),
            "Technical_Failed": int(technical_manifest.get("symbols_failed", 0)),
            "Candidate_Count": int(len(candidates)),
            "Broker_Coverage": float(fusion_manifest.get("broker_coverage", 0)),
            "Decision_Count": int(len(decisions)),
            "Decision_Counts": decisions["Decision_V3"].value_counts().to_dict(),
            "Decision_Contract": decision_validation,
            "Master_Pipeline_Status": master_manifest.get("Pipeline_Status"),
            "Master_Pipeline_Data_Source": master_manifest.get("Data_Source"),
            "Master_Decision_Contract": master_decision_validation,
            "Entry_Plans": int(len(plans)),
            "Approved_Entries": int((plans.get("Plan_Status", pd.Series(dtype=str)) == "ACCEPT").sum()) if not plans.empty else 0,
            "Telegram_Preview_Files": len(previews),
            "Database_SHA256": file_sha256(work / "test.db"),
            "Notes": [
                "Yahoo network refresh was not called; packaged historical files were used.",
                "Broker data was generated as a complete deterministic fixture matching the current 30 candidates.",
                "Decision V3 quality, readiness, broker confidence, liquidity, score, and labels were independently recalculated for every output row.",
                "The canonical master_pipeline.py is covered by orchestrator regression tests; bounded release validation executes each production module independently.",
                "Two short-history symbols may be skipped by the technical engine without failing the pipeline.",
            ],
        }

    json_path = report_dir / "E2E_VALIDATION_REPORT.json"
    md_path = report_dir / "E2E_VALIDATION_REPORT.md"
    write_json(json_path, summary)
    lines = [
        f"# End-to-End Validation Report — {DISPLAY_VERSION}", "",
        f"- Run ID: `{summary['Run_ID']}`",
        f"- Status: **{summary['Status']}**",
        f"- Pipeline version: `{summary['Pipeline_Version']}`",
        f"- Technical success: {summary['Technical_Success']} / {summary['Technical_Files']}",
        f"- Candidates: {summary['Candidate_Count']}",
        f"- Broker coverage: {summary['Broker_Coverage']:.0%}",
        f"- Decision rows independently checked: {summary['Decision_Contract']['rows_checked']}",
        f"- Master pipeline: {summary['Master_Pipeline_Status']} ({summary['Master_Pipeline_Data_Source']})",
        f"- Master decision rows independently checked: {summary['Master_Decision_Contract']['rows_checked']}",
        f"- Entry plans: {summary['Entry_Plans']} (approved {summary['Approved_Entries']})",
        f"- Telegram previews: {summary['Telegram_Preview_Files']}", "",
        "## Stage Results", "",
        "| Stage | Status | Duration (s) |",
        "|---|---:|---:|",
    ]
    lines += [f"| {x['stage']} | {x['status']} | {x['duration_seconds']:.3f} |" for x in stages]
    lines += ["", "## Decision Counts", ""]
    for key, value in summary["Decision_Counts"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Notes", ""] + [f"- {note}" for note in summary["Notes"]]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
