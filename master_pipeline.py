#!/usr/bin/env python3
"""Deprecated compatibility entry point.

Use ``run_sde_job.py`` for the V1.7.1 integrated runtime.  This module stays
available because the Stage 1/2 command contract and regression fixtures still
invoke it through ``job_full_manual``.
"""

from __future__ import annotations

DEPRECATED_COMPATIBILITY_ENTRYPOINT = True

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from swing_utils import DISPLAY_VERSION, PIPELINE_VERSION, file_sha256, make_run_id, read_json, write_json
from modules.runtime_config import load_runtime_config, write_runtime_config_audit

ROOT = Path(__file__).resolve().parent


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(value: str | Path) -> Path:
    p = Path(os.path.expandvars(str(value)))
    return p if p.is_absolute() else ROOT / p


def show(title: str) -> None:
    print("\n" + "=" * 64 + f"\n {title}\n" + "=" * 64)


def require(path: Path, label: str) -> None:
    if not path.exists() or (path.is_file() and path.stat().st_size == 0):
        raise FileNotFoundError(f"{label} tidak ditemukan atau kosong: {path}")


def run_command(name: str, command: list[str], log_file: Path) -> None:
    print(f"\n[RUN] {name}")
    print("      " + " ".join(f'"{x}"' if " " in x else x for x in command))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {name}\nCOMMAND: " + " ".join(command) + "\n")
        p = subprocess.Popen(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert p.stdout
        for line in p.stdout:
            print(line, end="", flush=True)
            fh.write(line)
            fh.flush()
        code = p.wait()
        fh.write(f"RETURN_CODE: {code}\n")
    if code:
        raise RuntimeError(f"{name} gagal (exit code {code})")
    print(f"[OK ] {name}")


def latest_manifest(manifest_dir: Path, prefix: str, run_id: str) -> dict[str, Any]:
    return read_json(manifest_dir / f"{prefix}_{run_id}.json")


def csv_date(path: Path, *aliases: str) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return ""
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:
        return ""
    for alias in aliases or ("Date",):
        matches = [c for c in df.columns if str(c).strip().lower() == alias.lower()]
        if matches:
            parsed = pd.to_datetime(df[matches[0]], errors="coerce")
            valid = parsed.dropna()
            return valid.max().date().isoformat() if not valid.empty else ""
    return ""


def quality_from(*values: str) -> str:
    priority = [
        "INVALID",
        "PROVIDER_FAILED",
        "BROKER_DATE_OVERRIDE",
        "STALE_ACCEPTED",
        "PARTIAL_COVERAGE",
        "PARTIAL_CANDLE",
        "MANUAL_FILE",
        "VALID",
    ]
    normalized = [str(v or "VALID").upper() for v in values]
    for item in priority:
        if item in normalized:
            return item
    return "VALID"


def write_run_manifest(path: Path, manifest: dict[str, Any]) -> None:
    write_json(path, manifest)


def inspect_existing_decision_source(path: Path) -> dict[str, Any]:
    """Validate the reusable V2 source before decision-only execution."""
    require(path, "FINAL_DECISION_V2.csv")
    df = pd.read_csv(path, low_memory=False)
    if df.empty:
        raise RuntimeError(f"Decision source kosong: {path}")

    available_col = next((c for c in df.columns if str(c).strip().lower() == "broker_data_available"), None)
    if available_col:
        available = df[available_col].astype(str).str.upper().str.strip().isin({"TRUE", "1", "YES", "YA"})
    else:
        confirmation_col = next((c for c in df.columns if str(c).strip().lower() == "broker_confirmation"), None)
        available = (~df[confirmation_col].fillna("").astype(str).str.upper().isin({"", "NO DATA"})) if confirmation_col else pd.Series(False, index=df.index)

    quality_col = next((c for c in df.columns if str(c).strip().lower() == "data_quality_status"), None)
    quality_values = df[quality_col].dropna().astype(str).tolist() if quality_col else ["VALID"]
    return {
        "rows": int(len(df)),
        "matched": int(available.sum()),
        "coverage": float(available.mean()) if len(df) else 0.0,
        "technical_date": csv_date(path, "Technical_Data_Date", "Date"),
        "broker_date": csv_date(path, "Broker_Data_Date", "TO_DATE_BROKER", "TO_DATE"),
        "data_quality": quality_from(*quality_values),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{DISPLAY_VERSION} pipeline")
    ap.add_argument("--config", default="config/pipeline.json")
    ap.add_argument("--refresh-data", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--skip-broker-wait", action="store_true")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--scheduler", action="store_true")
    ap.add_argument("--interactive-yahoo", action="store_true")
    ap.add_argument("--telegram-dry-run", action="store_true")
    ap.add_argument("--yahoo-failure-policy", choices=["STOP", "USE_LAST_VALID"], default=None)
    ap.add_argument("--broker-date-policy", choices=["exact", "latest", "manual", "ask"], default=None)
    ap.add_argument("--manual-broker-file", default="")
    ap.add_argument("--test-fixture", action="store_true", help="Gunakan fixture lokal; bukan production")
    ap.add_argument("--fixture-dir", default="", help="Folder fixture Yahoo; hanya untuk --test-fixture")
    ap.add_argument("--force-refresh", action="store_true", help="Paksa Yahoo incremental overlap refresh")
    ap.add_argument("--full-backfill", action="store_true", help="Paksa Yahoo full historical download")
    ap.add_argument(
        "--evaluation-datetime",
        default="",
        help="Override waktu evaluasi ISO untuk replay/test deterministik",
    )
    args = ap.parse_args()

    config_path = resolve(args.config)
    cfg, config_provenance = load_runtime_config(config_path, strict=True)
    paths = cfg["paths"]
    run_id = args.run_id or make_run_id()
    log = resolve(cfg.get("logging", {}).get("pipeline_log", "logs/master_pipeline.log"))
    manifest_dir = resolve(paths.get("manifest_dir", "data/output/manifests"))
    manifest_dir.mkdir(parents=True, exist_ok=True)
    run_manifest_path = manifest_dir / f"SWING_RUN_MANIFEST_{run_id}.json"
    config_audit_path = manifest_dir / f"RUNTIME_CONFIG_{run_id}.json"
    write_runtime_config_audit(config_audit_path, config_provenance, cfg)
    config_provenance["audit_path"] = str(config_audit_path.resolve())

    started_at = datetime.now().isoformat(timespec="seconds")
    run_manifest: dict[str, Any] = {
        "Run_ID": run_id,
        "Strategy_Type": "SWING",
        "Pipeline_Version": PIPELINE_VERSION,
        "Config_Source": config_provenance.get("config_source", ""),
        "Config_Hash": config_provenance.get("config_hash", ""),
        "Config_Version": config_provenance.get("config_version", ""),
        "Config_Loaded_At": config_provenance.get("loaded_at", ""),
        "Config_Audit_Path": str(config_audit_path.resolve()),
        "Config_Override_Mode": config_provenance.get("override_mode", "NONE"),
        "Started_At": started_at,
        "Finished_At": "",
        "Pipeline_Status": "RUNNING",
        "Yahoo_Refresh_Status": "",
        "Yahoo_Refresh_Mode": "",
        "Yahoo_Already_Current_Count": 0,
        "Yahoo_Incremental_Update_Count": 0,
        "Yahoo_Full_Backfill_Count": 0,
        "Yahoo_Network_Request_Symbol_Count": 0,
        "Yahoo_Network_Request_Batch_Count": 0,
        "Historical_Latest_Valid_Date": "",
        "Technical_Date": "",
        "Candidate_Generated_At": "",
        "Candidate_Count": 0,
        "Candidate_Changed": None,
        "Broker_Date": "",
        "Broker_Coverage": "",
        "Broker_Date_Override": False,
        "Broker_File_Selected": "",
        "Fusion_Output": "",
        "Decision_Output": "",
        "Exit_Output": "",
        "Telegram_Status": "SKIPPED" if args.no_telegram else "PENDING",
        "Fallback_Used": False,
        "Data_Quality_Status": "VALID",
        "Warnings": [],
        "Errors": [],
        "Source_Files": {},
        "Output_Files": {},
    }
    write_run_manifest(run_manifest_path, run_manifest)

    show(DISPLAY_VERSION.upper())
    print("Run ID      :", run_id)
    print("Waktu mulai :", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    stage = "PRE-FLIGHT"
    data_quality = "VALID"
    try:
        hist = resolve(paths["historical_dir"])
        historical_root = hist.parent
        techdir = resolve(paths["technical_output_dir"])
        canddir = resolve(paths["candidate_output_dir"])
        ihsg = resolve(paths["ihsg_csv"])
        decision_source = resolve(paths["decision_source"])
        out = resolve(paths["decision_output_dir"])
        exitout = resolve(paths["exit_output_dir"])
        bcfg = cfg.get("broker", {})
        freshness = cfg.get("data_freshness", {})
        configured_source = str(freshness.get("data_source", "LIVE")).upper()
        data_source = "FIXTURE" if args.test_fixture or configured_source == "FIXTURE" else "LIVE"
        data_source_label = "OFFLINE_FIXTURE" if data_source == "FIXTURE" else "LIVE_YAHOO"
        run_mode_label = "TEST" if data_source == "FIXTURE" else "PRODUCTION"
        run_manifest["Data_Source"] = data_source_label
        run_manifest["Mode"] = run_mode_label
        print("Data Source :", data_source_label)
        print("Mode        :", run_mode_label)
        if data_source == "FIXTURE" and not args.fixture_dir:
            raise RuntimeError("Mode fixture membutuhkan --fixture-dir agar tidak tertukar dengan live provider")

        if args.refresh_data:
            yahoo_policy = args.yahoo_failure_policy or freshness.get("yahoo_failure_policy", "STOP")
            stage = "HISTORICAL DOWNLOADER"
            downloader_cmd = [
                sys.executable, "-u", str(resolve(paths["historical_downloader"])),
                str(resolve(paths["normalized_watchlist"])),
                "--output", str(historical_root),
                "--period", str(cfg.get("download", {}).get("period", "2y")),
                "--pause", str(cfg.get("download", {}).get("pause", 0.35)),
                "--run-id", run_id,
                "--manifest-dir", str(manifest_dir),
                "--daily-candle-policy", freshness.get("daily_candle_policy", "LAST_CLOSED_CANDLE"),
                "--yahoo-failure-policy", yahoo_policy,
                "--market-close", freshness.get("market_close", "16:15"),
                "--after-midnight-cutoff", freshness.get("after_midnight_cutoff", "06:00"),
                "--data-source", data_source,
                "--repair-overlap-sessions", str(freshness.get("yahoo_repair_overlap_sessions", 5)),
                "--batch-size", str(freshness.get("yahoo_batch_size", 50)),
                "--max-workers", str(freshness.get("yahoo_max_workers", 4)),
                "--request-delay-seconds", str(freshness.get("yahoo_request_delay_seconds", 1)),
                "--max-retries", str(freshness.get("yahoo_max_retries", cfg.get("download", {}).get("retries", 3))),
            ]
            if args.evaluation_datetime:
                downloader_cmd += ["--evaluation-datetime", args.evaluation_datetime]
            if freshness.get("yahoo_batch_enabled", True):
                downloader_cmd.append("--batch-enabled")
            if args.force_refresh or freshness.get("yahoo_force_refresh", False):
                downloader_cmd.append("--force-refresh")
            if args.full_backfill or freshness.get("yahoo_full_backfill", False):
                downloader_cmd.append("--full-backfill")
            if data_source == "FIXTURE":
                downloader_cmd += ["--test-fixture", "--fixture-dir", args.fixture_dir]
            if freshness.get("allow_partial_daily_candle", False):
                downloader_cmd.append("--allow-partial-daily-candle")
            if args.interactive_yahoo:
                downloader_cmd.append("--interactive")
            for holiday in freshness.get("market_holidays", []):
                downloader_cmd += ["--market-holiday", str(holiday)]
            for special_day in freshness.get("special_trading_days", []):
                downloader_cmd += ["--special-trading-day", str(special_day)]
            run_command(stage, downloader_cmd, log)
            yahoo_manifest = latest_manifest(manifest_dir, "YAHOO_REFRESH_MANIFEST", run_id)
            data_quality = quality_from(yahoo_manifest.get("Data_Quality_Status", "VALID"))
            run_manifest["Yahoo_Refresh_Status"] = yahoo_manifest.get("Refresh_Status", "")
            run_manifest["Yahoo_Refresh_Mode"] = yahoo_manifest.get("Refresh_Mode", "")
            run_manifest["Yahoo_Already_Current_Count"] = yahoo_manifest.get("Already_Current_Count", 0)
            run_manifest["Yahoo_Incremental_Update_Count"] = yahoo_manifest.get("Incremental_Update_Count", 0)
            run_manifest["Yahoo_Full_Backfill_Count"] = yahoo_manifest.get("Full_Backfill_Count", 0)
            run_manifest["Yahoo_Network_Request_Symbol_Count"] = yahoo_manifest.get("Network_Request_Symbol_Count", 0)
            run_manifest["Yahoo_Network_Request_Batch_Count"] = yahoo_manifest.get("Network_Request_Batch_Count", 0)
            run_manifest["Historical_Latest_Valid_Date"] = yahoo_manifest.get("Latest_Valid_Close_Date", "")
            run_manifest["Fallback_Used"] = bool(yahoo_manifest.get("Fallback_Used", False))
            if yahoo_manifest.get("Warning"):
                run_manifest["Warnings"].append(yahoo_manifest["Warning"])
            run_manifest["Data_Quality_Status"] = data_quality
            run_manifest["Source_Files"]["Yahoo_Universe"] = yahoo_manifest.get("Universe_Source", "")
            run_manifest["Output_Files"]["Yahoo_Manifest"] = str(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{run_id}.json")
            write_run_manifest(run_manifest_path, run_manifest)

            if data_source == "FIXTURE":
                stage = "IHSG FIXTURE VALIDATION"
                require(ihsg, "IHSG fixture CSV")
                run_manifest["Warnings"].append("TEST_FIXTURE_MODE; IHSG updater network call was skipped")
                write_run_manifest(run_manifest_path, run_manifest)
                print(f"[OK ] IHSG fixture: {ihsg}")
            else:
                stage = "IHSG UPDATER"
                run_command(stage, [
                    sys.executable, "-u", str(resolve(paths["ihsg_updater"])),
                    "--output", str(ihsg),
                    "--period", str(cfg.get("download", {}).get("period", "2y")),
                ], log)

            stage = "TECHNICAL FEATURE ENGINE"
            tech_cmd = [
                sys.executable, "-u", str(resolve(paths["technical_feature_engine"])),
                "--input", str(hist),
                "--output", str(techdir),
                "--run-id", run_id,
                "--manifest-dir", str(manifest_dir),
                "--data-quality-status", data_quality,
            ]
            if freshness.get("allow_partial_daily_candle", False):
                tech_cmd.append("--allow-partial-daily-candle")
            run_command(stage, tech_cmd, log)
            tech_manifest = latest_manifest(manifest_dir, "TECHNICAL_MANIFEST", run_id)
            run_manifest["Technical_Date"] = tech_manifest.get("Latest_Valid_Candle_Date", "")
            run_manifest["Output_Files"]["Technical"] = tech_manifest.get("latest_output", "")
            write_run_manifest(run_manifest_path, run_manifest)

            stage = "TECHNICAL CANDIDATE SELECTOR"
            run_command(stage, [
                sys.executable, "-u", str(resolve(paths["candidate_selector"])),
                str(techdir / "latest_technical_features.csv"),
                "--top", str(cfg.get("candidate", {}).get("top", 40)),
                "--min-score", str(cfg.get("candidate", {}).get("min_score", 58)),
                "--config", str(config_path),
                "--output-dir", str(canddir),
                "--run-id", run_id,
                "--manifest-dir", str(manifest_dir),
                "--data-quality-status", data_quality,
            ], log)
            candidate_manifest = latest_manifest(manifest_dir, "CANDIDATE_MANIFEST", run_id)
            run_manifest["Candidate_Generated_At"] = candidate_manifest.get("Candidate_Generated_At", "")
            run_manifest["Candidate_Count"] = candidate_manifest.get("Candidate_Count", 0)
            run_manifest["Candidate_Changed"] = candidate_manifest.get("Candidate_Changed", False)
            if candidate_manifest.get("Reason"):
                run_manifest["Warnings"].append(candidate_manifest["Reason"])
            run_manifest["Output_Files"]["Candidates"] = str(canddir / f"technical_candidates_top{cfg.get('candidate', {}).get('top', 40)}.csv")
            write_run_manifest(run_manifest_path, run_manifest)

            broker_symbols = canddir / "broker_symbols.csv"
            require(broker_symbols, "broker_symbols.csv")
            stage = "BROKER NAVIGATOR EXPORT"
            navigator_path = resolve(paths.get("broker_navigator_symbols", "data/output/candidates/BROKER_NAVIGATOR_SYMBOLS.csv"))
            run_command(stage, [
                sys.executable, "-u", str(resolve(paths["broker_navigator_export"])),
                str(canddir),
                "--output", str(navigator_path),
                "--run-id", run_id,
                "--manifest-dir", str(manifest_dir),
            ], log)
            navigator_manifest = latest_manifest(manifest_dir, "BROKER_NAVIGATOR_MANIFEST", run_id)
            actual_navigator = Path(navigator_manifest.get("output", str(navigator_path)))
            if navigator_manifest.get("warning"):
                run_manifest["Warnings"].append(navigator_manifest["warning"])
            run_manifest["Output_Files"]["Broker_Navigator"] = str(actual_navigator)
            print("\n[ACTION] Buka Stockbit, impor file berikut ke Tampermonkey, lalu klik Mulai / Resume:")
            print(f"         {actual_navigator}")

            if not args.skip_broker_wait:
                stage = "BROKER EXPORT WAIT"
                broker_policy = args.broker_date_policy
                if broker_policy is None:
                    broker_policy = bcfg.get("scheduler_broker_date_policy", "exact") if args.scheduler else bcfg.get("broker_date_policy", "ask")
                manual_broker_file = args.manual_broker_file or bcfg.get("manual_broker_file", "")
                cmd = [
                    sys.executable, "-u", str(resolve(paths["broker_bridge"])),
                    "--symbols", str(broker_symbols),
                    "--downloads", os.path.expandvars(str(bcfg.get("downloads_dir", "%USERPROFILE%/Downloads"))),
                    "--output", str(resolve(paths["broker_summary_latest"])),
                    "--raw-output", str(resolve(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))),
                    "--timeout", str(bcfg.get("timeout_seconds", 1200)),
                    "--poll", str(bcfg.get("poll_seconds", 2)),
                    "--min-coverage", str(bcfg.get("min_coverage", 0.8)),
                    "--expected-broker-date", run_manifest["Technical_Date"],
                    "--technical-date", run_manifest["Technical_Date"],
                    "--broker-date-policy", broker_policy,
                    "--run-id", run_id,
                    "--manifest-dir", str(manifest_dir),
                ]
                if bcfg.get("allow_old_date", False):
                    cmd.append("--allow-old-date")
                if broker_policy == "manual" and manual_broker_file:
                    cmd += ["--manual-broker-file", manual_broker_file]
                run_command(stage, cmd, log)
                broker_manifest = latest_manifest(manifest_dir, "BROKER_MANIFEST", run_id)
                broker_quality = broker_manifest.get("DATA_QUALITY_STATUS", "VALID")
                data_quality = quality_from(data_quality, broker_quality)
                run_manifest["Broker_Date"] = broker_manifest.get("broker_date", "")
                run_manifest["Broker_Coverage"] = f"{broker_manifest.get('matched', 0)}/{broker_manifest.get('expected', 0)} - {broker_manifest.get('coverage', 0):.0%}"
                run_manifest["Broker_Date_Override"] = bool(broker_manifest.get("BROKER_DATE_OVERRIDE", False))
                run_manifest["Broker_File_Selected"] = broker_manifest.get("BROKER_FILE_SELECTED", "")
                if broker_manifest.get("BROKER_WARNING"):
                    run_manifest["Warnings"].append(broker_manifest["BROKER_WARNING"])
                run_manifest["Data_Quality_Status"] = data_quality
                run_manifest["Output_Files"]["Broker_Manifest"] = str(manifest_dir / f"BROKER_MANIFEST_{run_id}.json")
                write_run_manifest(run_manifest_path, run_manifest)

            stage = "BROKER FUSION"
            candidate = canddir / f"technical_candidates_top{cfg.get('candidate', {}).get('top', 40)}.csv"
            fusion_cmd = [
                sys.executable, "-u", str(resolve(paths["broker_fusion"])),
                str(candidate),
                str(resolve(paths["broker_summary_latest"])),
                "--output", str(decision_source),
                "--run-id", run_id,
                "--manifest-dir", str(manifest_dir),
                "--data-quality-status", data_quality,
                "--min-coverage", str(bcfg.get("min_coverage", 0.80)),
                "--expected-broker-date", run_manifest.get("Technical_Date", ""),
            ]
            broker_policy_effective = args.broker_date_policy or (bcfg.get("scheduler_broker_date_policy", "exact") if args.scheduler else bcfg.get("broker_date_policy", "ask"))
            if bcfg.get("allow_partial_broker", False):
                fusion_cmd.append("--allow-partial-broker")
            if run_manifest.get("Broker_Date_Override") or bcfg.get("allow_old_date", False) or broker_policy_effective in {"latest", "manual"}:
                fusion_cmd.append("--allow-date-mismatch")
            run_command(stage, fusion_cmd, log)
            fusion_manifest = latest_manifest(manifest_dir, "BROKER_FUSION_MANIFEST", run_id)
            data_quality = quality_from(data_quality, fusion_manifest.get("Data_Quality_Status", "VALID"))
            run_manifest["Broker_Date"] = fusion_manifest.get("broker_date", run_manifest.get("Broker_Date", ""))
            run_manifest["Broker_Coverage"] = f"{fusion_manifest.get('broker_matched', 0)}/{fusion_manifest.get('broker_expected', 0)} - {fusion_manifest.get('broker_coverage', 0):.0%}"
            run_manifest["Fusion_Output"] = str(decision_source)
            run_manifest["Data_Quality_Status"] = data_quality
            run_manifest["Output_Files"]["Fusion"] = str(decision_source)
            write_run_manifest(run_manifest_path, run_manifest)

        require(decision_source, "FINAL_DECISION_V2.csv")
        if not args.refresh_data:
            existing = inspect_existing_decision_source(decision_source)
            minimum_coverage = float(bcfg.get("min_coverage", 0.80))
            if existing["coverage"] < minimum_coverage:
                raise RuntimeError(
                    f"Existing FINAL_DECISION_V2 broker coverage {existing['matched']}/{existing['rows']} "
                    f"({existing['coverage']:.0%}) di bawah minimum {minimum_coverage:.0%}"
                )
            data_quality = quality_from(data_quality, existing["data_quality"])
            run_manifest["Technical_Date"] = existing["technical_date"]
            run_manifest["Broker_Date"] = existing["broker_date"]
            run_manifest["Candidate_Count"] = existing["rows"]
            run_manifest["Broker_Coverage"] = f"{existing['matched']}/{existing['rows']} - {existing['coverage']:.0%}"
            run_manifest["Data_Quality_Status"] = data_quality
            run_manifest["Warnings"].append("EXISTING_DECISION_SOURCE_REUSED; Yahoo, technical, candidate, and broker refresh were not run")
            write_run_manifest(run_manifest_path, run_manifest)
        require(ihsg, "IHSG.csv")
        out.mkdir(parents=True, exist_ok=True)
        stage = "DECISION ENGINE"
        run_command(stage, [
            sys.executable, "-u", str(resolve(paths["decision_engine"])),
            str(decision_source),
            str(out),
            "--ihsg", str(ihsg),
            "--config", str(config_path),
            "--run-id", run_id,
            "--manifest-dir", str(manifest_dir),
            "--data-quality-status", data_quality,
        ], log)
        final = out / "FINAL_DECISION_V3.csv"
        market = out / "MARKET_STATUS.json"
        require(final, "FINAL_DECISION_V3.csv")
        require(market, "MARKET_STATUS.json")
        run_manifest["Decision_Output"] = str(final)
        if not run_manifest.get("Technical_Date"):
            run_manifest["Technical_Date"] = csv_date(final, "Technical_Data_Date", "Date")
        if not run_manifest.get("Broker_Date"):
            run_manifest["Broker_Date"] = csv_date(final, "Broker_Data_Date", "TO_DATE_BROKER", "TO_DATE")
        run_manifest["Output_Files"]["Decision"] = str(final)
        write_run_manifest(run_manifest_path, run_manifest)

        stage = "EXIT ENGINE"
        run_command(stage, [
            sys.executable, "-u", str(resolve(paths["exit_engine"])),
            str(final),
            str(hist),
            "--state-file", str(resolve(paths["active_trades_state"])),
            "--output-dir", str(exitout),
            "--min-rr", str(cfg.get("exit", {}).get("min_rr", 1.0)),
            "--preferred-rr", str(cfg.get("exit", {}).get("preferred_rr", 2.0)),
            "--max-risk-pct", str(cfg.get("exit", {}).get("max_risk_pct", 7.0)),
            "--max-hold-days", str(cfg.get("exit", {}).get("max_hold_days", 20)),
            "--run-id", run_id,
            "--manifest-dir", str(manifest_dir),
            "--data-quality-status", data_quality,
            "--config", str(config_path),
        ], log)
        run_manifest["Exit_Output"] = str(exitout)
        run_manifest["Output_Files"]["Exit"] = str(exitout)
        write_run_manifest(run_manifest_path, run_manifest)

        analytics_output = resolve(paths.get("analytics_output_root", "data/output/analytics")) / run_id
        backtest_summary = analytics_output / "BACKTEST_SUMMARY.csv"
        watchlist_outcomes = analytics_output / "WATCHLIST_OUTCOMES.csv"
        if cfg.get("analytics", {}).get("enabled", True) and ihsg.exists():
            stage = "SWING ANALYTICS"
            run_command(stage, [
                sys.executable, "-u", str(resolve(paths["backtest_engine"])),
                str(final),
                str(hist),
                str(ihsg),
                "--output-dir", str(analytics_output),
                "--horizons", str(cfg.get("analytics", {}).get("horizons", "1,3,5,7,10,20")),
                "--entry-plans", str(exitout / "ENTRY_PLANS.csv"),
            ], log)
            run_manifest["Output_Files"]["Analytics"] = str(analytics_output)

        if cfg.get("analytics", {}).get("profile_comparison_enabled", True):
            stage = "MODERATE PROFILE SHADOW"
            shadow_output = resolve(paths.get("profile_shadow_output_root", "data/output/profile_shadow")) / run_id
            shadow_cmd = [
                sys.executable, "-u", str(resolve(paths.get("profile_shadow_runner", "tools/run_moderate_shadow.py"))),
                str(final),
                "--entry-plans", str(exitout / "ENTRY_PLANS.csv"),
                "--config", str(config_path),
                "--output-dir", str(shadow_output),
                "--run-id", run_id,
            ]
            if watchlist_outcomes.exists() and watchlist_outcomes.stat().st_size > 0:
                shadow_cmd.extend(["--outcomes", str(watchlist_outcomes)])
            run_command(stage, shadow_cmd, log)
            run_manifest["Output_Files"]["Profile_Shadow"] = str(shadow_output)
            run_manifest["Shadow_Mode"] = "SHADOW_ONLY"
            run_manifest["Auto_Entry_Enabled"] = False

        run_manifest["Finished_At"] = datetime.now().isoformat(timespec="seconds")
        run_manifest["Pipeline_Status"] = "SUCCESS"
        run_manifest["Data_Quality_Status"] = data_quality
        write_run_manifest(run_manifest_path, run_manifest)

        if cfg.get("database", {}).get("enabled", True):
            stage = "SWING DATABASE ARCHIVE"
            yahoo_manifest_path = manifest_dir / f"YAHOO_REFRESH_MANIFEST_{run_id}.json"
            broker_manifest_path = manifest_dir / f"BROKER_MANIFEST_{run_id}.json"
            db_summary = manifest_dir / f"DATABASE_ARCHIVE_MANIFEST_{run_id}.json"
            run_command(stage, [
                sys.executable, "-u", str(resolve(paths["database_archiver"])),
                "--run-id", run_id,
                "--db", str(resolve(paths["swing_database"])),
                "--run-manifest", str(run_manifest_path),
                "--yahoo-manifest", str(yahoo_manifest_path),
                "--broker-manifest", str(broker_manifest_path),
                "--historical-dir", str(hist),
                "--technical", str(techdir / "latest_technical_features.csv"),
                "--candidates", str(canddir / f"technical_candidates_top{cfg.get('candidate', {}).get('top', 40)}.csv"),
                "--broker-summary", str(resolve(paths["broker_summary_latest"])),
                "--fusion", str(decision_source),
                "--decision", str(final),
                "--exit-dir", str(exitout),
                "--data-quality-status", data_quality,
                "--summary-output", str(db_summary),
            ], log)
            run_manifest["Output_Files"]["Database"] = str(resolve(paths["swing_database"]))
            write_run_manifest(run_manifest_path, run_manifest)

        if not args.no_telegram:
            stage = "TELEGRAM SWING REPORT"
            reports_dir = resolve(paths.get("reports_root", "data/output/reports")) / run_id
            preview_dir = resolve(paths.get("telegram_preview_root", "data/output/telegram_preview")) / run_id
            telegram_config = read_json(resolve(paths["telegram_config"]))
            dry_run = args.telegram_dry_run or bool(telegram_config.get("telegram", {}).get("dry_run", False))
            cmd = [
                sys.executable, "-u", str(resolve(paths["telegram_bot"])),
                "--config", str(resolve(paths["telegram_config"])),
            ]
            if dry_run:
                cmd.append("--dry-run")
            cmd += [
                "swing",
                "--run-id", run_id,
                "--run-manifest", str(run_manifest_path),
                "--decisions", str(final),
                "--entry-plans", str(exitout / "ENTRY_PLANS.csv"),
                "--market-status", str(market),
                "--exit-alerts", str(exitout / "EXIT_ALERTS.csv"),
                "--watchlist-outcomes", str(watchlist_outcomes),
                "--backtest-summary", str(backtest_summary),
                "--reports-dir", str(reports_dir),
                "--preview-dir", str(preview_dir),
            ]
            run_command(stage, cmd, log)
            run_manifest["Telegram_Status"] = "DRY_RUN" if dry_run else "SUCCESS"
            run_manifest["Output_Files"]["Telegram_Reports"] = str(reports_dir)
            if dry_run:
                run_manifest["Output_Files"]["Telegram_Preview"] = str(preview_dir)
            write_run_manifest(run_manifest_path, run_manifest)

        show("PIPELINE SELESAI")
        print(f"[OK] {DISPLAY_VERSION} selesai")
        print("Run manifest:", run_manifest_path)
        return 0
    except Exception as exc:
        run_manifest["Finished_At"] = datetime.now().isoformat(timespec="seconds")
        run_manifest["Pipeline_Status"] = "FAILED"
        run_manifest["Errors"].append({"stage": stage, "error": str(exc)})
        run_manifest["Data_Quality_Status"] = quality_from(run_manifest.get("Data_Quality_Status", "VALID"), "INVALID")
        write_run_manifest(run_manifest_path, run_manifest)
        print(f"\n[FAILED] {stage}: {exc}", file=sys.stderr)
        print(f"Log: {log}")
        print(f"Run manifest: {run_manifest_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
