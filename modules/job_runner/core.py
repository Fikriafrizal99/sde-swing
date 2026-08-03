from __future__ import annotations

import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from swing_utils import file_sha256, find_col, read_json as read_json_safely, write_json

from .runtime import RunnerContext, append_job_log, now_wib, resolve


READY = "READY"


def run_command(ctx: RunnerContext, name: str, command: list[str]) -> None:
    append_job_log(ctx, "COMMAND_START", name)
    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{now_wib().isoformat(timespec='seconds')}] {name}\n")
        handle.write("COMMAND: " + " ".join(command) + "\n")
        proc = subprocess.Popen(
            command,
            cwd=resolve("."),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout
        for line in proc.stdout:
            print(line, end="", flush=True)
            handle.write(line)
            handle.flush()
        proc.stdout.close()
        code = proc.wait()
        handle.write(f"RETURN_CODE: {code}\n")
    if code:
        append_job_log(ctx, "COMMAND_FAILED", f"{name}: {code}")
        raise RuntimeError(f"{name} gagal (exit code {code})")
    append_job_log(ctx, "COMMAND_OK", name)



def sync_outcome_tracker(
    ctx: RunnerContext,
    decision_path: Path | None = None,
    entry_plans_path: Path | None = None,
    signal_date: str = "",
) -> None:
    """Persist current signals and refresh all open outcomes.

    This is intentionally non-scheduler-specific. Callers decide whether a
    failure should block the main report; the normal jobs treat analytics as a
    non-blocking validation layer.
    """
    if ctx.dry_run:
        append_job_log(ctx, "OUTCOME_TRACKER_SKIPPED", "DRY_RUN")
        return
    paths = ctx.config.get("paths", {})
    analytics_root = resolve(paths.get("analytics_output_root", "data/output/analytics")) / "performance"
    cmd = [
        sys.executable,
        "-u",
        str(resolve("modules/analytics/outcome_tracker.py")),
        "sync",
        "--db",
        str(resolve(paths.get("swing_database", "data/database/sde_swing_history.db"))),
        "--historical-dir",
        str(resolve(paths.get("historical_dir", "data/output/historical/by_symbol"))),
        "--output-dir",
        str(analytics_root),
        "--bootstrap-db",
    ]
    if decision_path is not None:
        cmd += [
            "--decisions", str(decision_path),
            "--entry-plans", str(entry_plans_path or resolve(paths.get("exit_output_dir", "data/output/exit")) / "ENTRY_PLANS.csv"),
            "--run-id", ctx.run_id,
        ]
        if signal_date:
            cmd += ["--signal-date", signal_date]
    run_command(ctx, "OUTCOME TRACKER", cmd)

def run_master_pipeline(
    ctx: RunnerContext,
    refresh_data: bool,
    interactive_yahoo: bool = False,
    scheduler: bool = True,
    skip_broker_wait: bool = False,
    broker_date_policy: str | None = None,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-u",
        str(resolve("master_pipeline.py")),
        "--config",
        str(ctx.config_path),
        "--run-id",
        ctx.run_id,
        "--no-telegram",
    ]
    if refresh_data:
        cmd.append("--refresh-data")
    if interactive_yahoo:
        cmd.append("--interactive-yahoo")
    if scheduler:
        cmd.append("--scheduler")
    if skip_broker_wait:
        cmd.append("--skip-broker-wait")
    if broker_date_policy:
        cmd += ["--broker-date-policy", broker_date_policy]
    run_command(ctx, "MASTER PIPELINE", cmd)
    return read_json_safely(ctx.path("manifest_dir", "data/output/manifests") / f"SWING_RUN_MANIFEST_{ctx.run_id}.json")


def _csv_latest_date(path: Path, aliases: tuple[str, ...]) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return ""
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:
        return ""
    col = find_col(df, *aliases)
    if not col:
        return ""
    parsed = pd.to_datetime(df[col], errors="coerce").dropna()
    return parsed.max().date().isoformat() if not parsed.empty else ""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _stage_paths(ctx: RunnerContext) -> dict[str, Path]:
    paths = ctx.config.get("paths", {})
    if ctx.dry_run:
        root = resolve("data/output/dry_run") / ctx.run_id
        return {
            "historical_root": root / "historical",
            "historical_by_symbol": root / "historical/by_symbol",
            "technical_dir": root / "technical",
            "candidate_dir": root / "candidates",
            "manifest_dir": root / "manifests",
            "ihsg": resolve(paths.get("ihsg_csv", "data/input/IHSG.csv")),
            "snapshot_root": root / "snapshots",
        }
    historical_by_symbol = resolve(paths.get("historical_dir", "data/output/historical/by_symbol"))
    return {
        "historical_root": historical_by_symbol.parent,
        "historical_by_symbol": historical_by_symbol,
        "technical_dir": resolve(paths.get("technical_output_dir", "data/output/technical")),
        "candidate_dir": resolve(paths.get("candidate_output_dir", "data/output/candidates")),
        "manifest_dir": resolve(paths.get("manifest_dir", "data/output/manifests")),
        "ihsg": resolve(paths.get("ihsg_csv", "data/input/IHSG.csv")),
        "snapshot_root": resolve("data/output/snapshots"),
    }


def run_post_market_technical_stage(ctx: RunnerContext) -> dict[str, Any]:
    # Provider readiness is centralized even while the legacy downloader
    # remains the compatibility execution engine for Stage 1/2.
    source_metadata = ctx.source_manager.provider_metadata(record_type="DailyBar")
    append_job_log(ctx, "POST_MARKET_SOURCE_MANAGER_READY", str({
        "provider_status": source_metadata.get("provider_status"),
        "data_source_mode": source_metadata.get("data_source_mode"),
    }))
    cfg = ctx.config
    paths = cfg.get("paths", {})
    freshness = cfg.get("data_freshness", {})
    stage_paths = _stage_paths(ctx)
    manifest_dir = stage_paths["manifest_dir"]
    manifest_dir.mkdir(parents=True, exist_ok=True)
    data_source = str(freshness.get("data_source", "LIVE")).upper()
    if data_source == "FIXTURE" and not ctx.dry_run:
        raise RuntimeError("Live post_market menolak data_source FIXTURE")
    evaluation_dt = f"{ctx.trade_date.isoformat()}T17:00:00+07:00"
    yahoo_policy = freshness.get("yahoo_failure_policy", "STOP")
    downloader_cmd = [
        sys.executable,
        "-u",
        str(resolve(paths.get("historical_downloader", "modules/historical_downloader/historical_downloader.py"))),
        str(resolve(paths.get("normalized_watchlist", "modules/historical_downloader/Stockbit_Watchlist_2026-07-19_normalized.csv"))),
        "--output",
        str(stage_paths["historical_root"]),
        "--period",
        str(cfg.get("download", {}).get("period", "2y")),
        "--pause",
        str(cfg.get("download", {}).get("pause", 0.35)),
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
        "--daily-candle-policy",
        freshness.get("daily_candle_policy", "LAST_CLOSED_CANDLE"),
        "--yahoo-failure-policy",
        yahoo_policy,
        "--market-close",
        freshness.get("market_close", "16:15"),
        "--after-midnight-cutoff",
        freshness.get("after_midnight_cutoff", "06:00"),
        "--data-source",
        data_source if data_source == "FIXTURE" else "LIVE",
        "--incremental-overlap-days",
        str(freshness.get("yahoo_incremental_overlap_days", 5)),
        "--batch-size",
        str(freshness.get("yahoo_batch_size", 50)),
        "--max-workers",
        str(freshness.get("yahoo_max_workers", 4)),
        "--request-delay-seconds",
        str(freshness.get("yahoo_request_delay_seconds", 1)),
        "--max-retries",
        str(freshness.get("yahoo_max_retries", cfg.get("download", {}).get("retries", 3))),
        "--evaluation-datetime",
        evaluation_dt,
    ]
    if freshness.get("yahoo_batch_enabled", True):
        downloader_cmd.append("--batch-enabled")
    if data_source == "FIXTURE":
        fixture_dir = str(freshness.get("fixture_dir", "")) or str(resolve(paths.get("historical_dir", "data/output/historical/by_symbol")))
        downloader_cmd += ["--test-fixture", "--fixture-dir", fixture_dir]
    for holiday in freshness.get("market_holidays", []):
        downloader_cmd += ["--market-holiday", str(holiday)]
    run_command(ctx, "POST MARKET HISTORICAL DOWNLOADER", downloader_cmd)

    yahoo_manifest = read_json_safely(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{ctx.run_id}.json")
    data_quality = str(yahoo_manifest.get("Data_Quality_Status", "VALID"))
    if data_source != "FIXTURE":
        run_command(ctx, "POST MARKET IHSG UPDATER", [
            sys.executable,
            "-u",
            str(resolve(paths.get("ihsg_updater", "modules/market_data/update_ihsg.py"))),
            "--output",
            str(stage_paths["ihsg"]),
            "--period",
            str(cfg.get("download", {}).get("period", "2y")),
        ])

    run_command(ctx, "POST MARKET TECHNICAL FEATURE ENGINE", [
        sys.executable,
        "-u",
        str(resolve(paths.get("technical_feature_engine", "modules/technical_feature_engine/technical_feature_engine.py"))),
        "--input",
        str(stage_paths["historical_by_symbol"]),
        "--output",
        str(stage_paths["technical_dir"]),
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
        "--data-quality-status",
        data_quality,
    ])
    tech_manifest = read_json_safely(manifest_dir / f"TECHNICAL_MANIFEST_{ctx.run_id}.json")

    run_command(ctx, "POST MARKET TECHNICAL CANDIDATE SELECTOR", [
        sys.executable,
        "-u",
        str(resolve(paths.get("candidate_selector", "modules/candidate_selector/technical_candidate_selector.py"))),
        str(stage_paths["technical_dir"] / "latest_technical_features.csv"),
        "--top",
        str(cfg.get("candidate", {}).get("top", 40)),
        "--min-score",
        str(cfg.get("candidate", {}).get("min_score", 58)),
        "--config",
        str(ctx.config_path),
        "--output-dir",
        str(stage_paths["candidate_dir"]),
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
        "--data-quality-status",
        data_quality,
    ])
    candidate_manifest = read_json_safely(manifest_dir / f"CANDIDATE_MANIFEST_{ctx.run_id}.json")
    navigator_path = export_broker_navigator_symbols(ctx, stage_paths, manifest_dir)
    snapshot = create_technical_snapshot(
        ctx,
        stage_paths,
        yahoo_manifest,
        tech_manifest,
        candidate_manifest,
        navigator_path=navigator_path,
    )
    return {
        "Run_ID": ctx.run_id,
        "Pipeline_Status": "SUCCESS",
        "Stage": "POST_MARKET_TECHNICAL_ONLY",
        "Data_Source": yahoo_manifest.get("Data_Source", "LIVE_YAHOO"),
        "Data_Quality_Status": data_quality,
        "Technical_Date": snapshot["trade_date"],
        "Snapshot_ID": snapshot["snapshot_id"],
        "Snapshot_Manifest": snapshot["manifest_path"],
        "Candidate_Count": candidate_manifest.get("Candidate_Count", 0),
        "symbols_requested": snapshot.get("symbols_requested", 0),
        "symbols_loaded": snapshot.get("symbols_loaded", 0),
        "symbols_valid": snapshot.get("symbols_valid", 0),
        "symbols_failed": snapshot.get("symbols_failed", 0),
        "symbols_skipped": snapshot.get("symbols_skipped", 0),
        "provider_status": source_metadata.get("provider_status", "NOT_CONFIGURED"),
        "data_source_mode": source_metadata.get("data_source_mode", "NOT_CONFIGURED"),
        "source_coverage_ratio": (snapshot.get("symbols_valid", 0) / snapshot.get("symbols_requested", 1)) if snapshot.get("symbols_requested", 0) else 0.0,
        "snapshot_ids": {"technical": snapshot.get("snapshot_id", "")},
        "Config_Version": ctx.runtime_version,
        "Broker_Navigator_Path": str(navigator_path),
        "Warnings": [x for x in [yahoo_manifest.get("Warning"), candidate_manifest.get("Reason")] if x],
        "Output_Files": snapshot.get("output_paths", {}),
        "source_metadata": source_metadata,
    }


def export_broker_navigator_symbols(
    ctx: RunnerContext,
    stage_paths: dict[str, Path],
    manifest_dir: Path,
) -> Path:
    """Create the current Tampermonkey symbol bridge after every Post Market scan."""
    paths = ctx.config.get("paths", {})
    source = stage_paths["candidate_dir"] / "broker_symbols.csv"
    if ctx.dry_run:
        requested_output = stage_paths["candidate_dir"] / "BROKER_NAVIGATOR_SYMBOLS.csv"
    else:
        requested_output = resolve(
            paths.get("broker_navigator_symbols", "data/output/candidates/BROKER_NAVIGATOR_SYMBOLS.csv")
        )
    run_command(ctx, "POST MARKET BROKER NAVIGATOR EXPORT", [
        sys.executable,
        "-u",
        str(resolve(paths.get("broker_navigator_export", "modules/broker_bridge/broker_navigator_export.py"))),
        str(source),
        "--output",
        str(requested_output),
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
    ])
    export_manifest = read_json_safely(manifest_dir / f"BROKER_NAVIGATOR_MANIFEST_{ctx.run_id}.json")
    actual_output = Path(str(export_manifest.get("output") or requested_output))
    if not actual_output.exists():
        raise RuntimeError(f"BROKER_NAVIGATOR_SYMBOLS tidak terbentuk: {actual_output}")
    return actual_output


def create_technical_snapshot(
    ctx: RunnerContext,
    stage_paths: dict[str, Path],
    yahoo_manifest: dict[str, Any],
    tech_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
    navigator_path: Path | None = None,
) -> dict[str, Any]:
    snapshot_id = f"SWING-TECH-SNAPSHOT-{ctx.trade_date.strftime('%Y%m%d')}-{now_wib().strftime('%H%M%S')}"
    root = stage_paths["snapshot_root"] / ctx.trade_date.isoformat() / snapshot_id
    root.mkdir(parents=True, exist_ok=True)
    technical = stage_paths["technical_dir"] / "latest_technical_features.csv"
    candidates = stage_paths["candidate_dir"] / f"technical_candidates_top{ctx.config.get('candidate', {}).get('top', 40)}.csv"
    ranking = stage_paths["candidate_dir"] / "technical_ranking_full.csv"
    broker_symbols = stage_paths["candidate_dir"] / "broker_symbols.csv"
    copied: dict[str, str] = {}
    for label, source in {
        "technical_features": technical,
        "technical_candidates": candidates,
        "technical_ranking": ranking,
        "broker_symbols": broker_symbols,
        "broker_navigator_symbols": navigator_path,
    }.items():
        if source is not None and source.exists():
            destination = root / source.name
            shutil.copy2(source, destination)
            copied[label] = str(destination)
    feature_status = stage_paths["technical_dir"] / "logs" / "feature_status.csv"
    failed_symbols = stage_paths["technical_dir"] / "logs" / "failed_symbols.csv"
    technical_date = (
        str(tech_manifest.get("Latest_Valid_Candle_Date") or "")
        or _csv_latest_date(technical, ("Latest_Valid_Candle_Date", "Date", "Technical_Data_Date"))
    )
    status_df = _read_csv(feature_status)
    symbols_loaded = int(tech_manifest.get("symbols_total", 0) or tech_manifest.get("Technical_Symbol_Count", 0) or len(status_df))
    symbols_failed = int(tech_manifest.get("symbols_failed", 0) or (len(_read_csv(failed_symbols)) if failed_symbols.exists() else 0))
    symbols_requested = int(
        tech_manifest.get("symbols_requested", 0)
        or tech_manifest.get("symbols_total_requested", 0)
        or candidate_manifest.get("Universe_Count", 0)
        or symbols_loaded + symbols_failed
    )
    symbols_valid = max(symbols_loaded - symbols_failed, 0)
    symbols_skipped = max(symbols_requested - symbols_loaded - symbols_failed, 0)
    payload = {
        "snapshot_id": snapshot_id,
        "run_id": ctx.run_id,
        "job": ctx.job,
        "trade_date": technical_date,
        "job_trade_date": ctx.trade_date.isoformat(),
        "created_at": now_wib().isoformat(timespec="seconds"),
        "source_provider": yahoo_manifest.get("Data_Source", "LIVE_YAHOO"),
        "historical_date": yahoo_manifest.get("Latest_Valid_Close_Date", ""),
        "ihsg_date": _csv_latest_date(stage_paths["ihsg"], ("Date",)),
        "technical_feature_date": technical_date,
        "candidate_date": candidate_manifest.get("Technical_Data_Date", technical_date),
        "symbols_loaded": symbols_loaded,
        "symbols_requested": symbols_requested,
        "symbols_valid": symbols_valid,
        "symbols_failed": symbols_failed,
        "symbols_skipped": symbols_skipped,
        "data_quality_status": tech_manifest.get("Data_Quality_Status", yahoo_manifest.get("Data_Quality_Status", "VALID")),
        "config_version": ctx.runtime_version,
        "source_metadata": {
            "provider": yahoo_manifest.get("Data_Source", "HISTORICAL_PROVIDER"),
            "provider_status": yahoo_manifest.get("Provider_Status", "FILE" if str(yahoo_manifest.get("Data_Source", "")).upper() != "LIVE_YAHOO" else "LIVE"),
            "data_source_mode": yahoo_manifest.get("Data_Source_Mode", "FILE" if str(yahoo_manifest.get("Data_Source", "")).upper() != "LIVE_YAHOO" else "LIVE"),
        },
        "broker_navigator_path": str(navigator_path) if navigator_path else "",
        "output_paths": copied,
        "file_hashes": {label: file_sha256(path) for label, path in copied.items()},
    }
    manifest_path = root / "snapshot_manifest.json"
    payload["manifest_path"] = str(manifest_path)
    import hashlib
    payload["content_hash"] = hashlib.sha256(
        __import__("json").dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    write_json(manifest_path, payload)
    index_root = resolve("data/output/snapshots") / ctx.trade_date.isoformat()
    if not ctx.dry_run:
        write_json(index_root / "latest_snapshot.json", payload)
    return payload


def load_technical_snapshot(ctx: RunnerContext) -> dict[str, Any]:
    snapshot_path = resolve("data/output/snapshots") / ctx.trade_date.isoformat() / "latest_snapshot.json"
    snapshot = read_json_safely(snapshot_path)
    if not snapshot:
        return {"status": "INVALID_DEPENDENCY", "reason": "TECHNICAL_SNAPSHOT_NOT_FOUND", "snapshot_manifest": str(snapshot_path)}
    snapshot_date = str(snapshot.get("trade_date", ""))
    if snapshot_date != ctx.trade_date.isoformat():
        snapshot["status"] = "STALE_TECHNICAL_SNAPSHOT"
        snapshot["reason"] = f"snapshot trade_date {snapshot_date} != job trade_date {ctx.trade_date.isoformat()}"
        return snapshot
    snapshot["status"] = "VALID"
    return snapshot


def _expected_symbols_from_snapshot(snapshot: dict[str, Any]) -> list[str]:
    path = Path(str(snapshot.get("output_paths", {}).get("technical_candidates", "")))
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:
        return []
    col = find_col(df, "Symbol", "Ticker", "EMITEN")
    if not col:
        return []
    return sorted(df[col].dropna().astype(str).str.upper().str.replace(".JK", "", regex=False).unique().tolist())


def validate_broker_summary(ctx: RunnerContext, expected_symbols: list[str] | None = None) -> dict[str, Any]:
    cfg = ctx.scheduler_config.get("broker_readiness", {})
    path = ctx.path("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv")
    expected_symbols = expected_symbols or []
    report: dict[str, Any] = {
        "run_id": ctx.run_id,
        "job": ctx.job,
        "checked_at": now_wib().isoformat(timespec="seconds"),
        "broker_file": str(path),
        "job_trade_date": ctx.trade_date.isoformat(),
        "status": "",
        "broker_date": "",
        "expected_symbols": len(expected_symbols),
        "matched_symbols": 0,
        "coverage_ratio": None,
        "errors": [],
    }
    if not path.exists():
        report["status"] = "FILE_NOT_FOUND"
        return _write_broker_readiness(ctx, report)
    size1 = path.stat().st_size
    if size1 == 0:
        report["status"] = "EMPTY_DATA"
        return _write_broker_readiness(ctx, report)
    stable_seconds = int(cfg.get("stable_file_check_seconds", 5))
    if stable_seconds > 0:
        time.sleep(stable_seconds)
    size2 = path.stat().st_size
    if size1 != size2:
        report["status"] = "FILE_WRITING"
        return _write_broker_readiness(ctx, report)
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        report["status"] = "PARSE_FAILED"
        report["errors"].append(str(exc))
        return _write_broker_readiness(ctx, report)
    if df.empty:
        report["status"] = "EMPTY_DATA"
        return _write_broker_readiness(ctx, report)
    required = [str(x) for x in cfg.get("required_broker_columns", ["EMITEN", "TO_DATE", "TOTAL_BUY", "TOTAL_SELL"])]
    missing = [col for col in required if find_col(df, col) is None]
    if missing:
        report["status"] = "SCHEMA_INVALID"
        report["errors"].append("missing columns: " + ",".join(missing))
        return _write_broker_readiness(ctx, report)
    symbol_col = find_col(df, "EMITEN", "Symbol", "Ticker")
    date_col = find_col(df, "TO_DATE", "TO_DATE_BROKER", "Broker_Data_Date")
    buy_col = find_col(df, "TOTAL_BUY")
    sell_col = find_col(df, "TOTAL_SELL")
    assert symbol_col and date_col and buy_col and sell_col
    symbols = df[symbol_col].dropna().astype(str).str.upper().str.replace(".JK", "", regex=False)
    if symbols.empty:
        report["status"] = "EMPTY_DATA"
        return _write_broker_readiness(ctx, report)
    broker_dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
    if broker_dates.empty:
        report["status"] = "DATE_MISMATCH"
        report["errors"].append("broker date cannot be parsed")
        return _write_broker_readiness(ctx, report)
    broker_date = broker_dates.max().date().isoformat()
    report["broker_date"] = broker_date
    if broker_date != ctx.trade_date.isoformat():
        report["status"] = "DATE_MISMATCH"
        return _write_broker_readiness(ctx, report)
    min_records = int(cfg.get("minimum_broker_records", 1))
    if len(df) < min_records:
        report["status"] = "INSUFFICIENT_COVERAGE"
        report["errors"].append(f"records {len(df)} < minimum {min_records}")
        return _write_broker_readiness(ctx, report)
    if bool(cfg.get("reject_sample_data", True)):
        sample_markers = ("sample", "fixture", "dummy", "placeholder")
        text_cols = df.astype(str).agg(" ".join, axis=1).str.lower()
        if text_cols.str.contains("|".join(sample_markers), regex=True).any():
            report["status"] = "SAMPLE_DATA_DETECTED"
            return _write_broker_readiness(ctx, report)
    dupes = sorted(symbols[symbols.duplicated()].unique().tolist())
    if dupes:
        report["duplicate_symbols"] = dupes
        report["status"] = "DUPLICATE_DATA"
        return _write_broker_readiness(ctx, report)
    for col in (buy_col, sell_col):
        numeric = pd.to_numeric(df[col], errors="coerce")
        if numeric.isna().any():
            report["status"] = "INVALID_NUMERIC_DATA"
            report["errors"].append(f"numeric parse failed: {col}")
            return _write_broker_readiness(ctx, report)
    if expected_symbols:
        matched = len(set(symbols.tolist()) & set(expected_symbols))
        coverage = matched / len(expected_symbols) if expected_symbols else 0.0
        report["matched_symbols"] = matched
        report["coverage_ratio"] = coverage
        if coverage < float(cfg.get("minimum_broker_coverage_ratio", 0.8)):
            report["status"] = "INSUFFICIENT_COVERAGE"
            return _write_broker_readiness(ctx, report)
    report["status"] = READY
    return _write_broker_readiness(ctx, report)


def _write_broker_readiness(ctx: RunnerContext, report: dict[str, Any]) -> dict[str, Any]:
    root = ctx.status_root
    write_json(root / f"{ctx.run_id}_broker_readiness.json", report)
    write_json(root / ctx.trade_date.isoformat() / f"{ctx.run_id}_broker_readiness.json", report)
    return report


def broker_readiness(ctx: RunnerContext) -> tuple[bool, dict[str, Any]]:
    snapshot = load_technical_snapshot(ctx)
    if snapshot.get("status") != "VALID":
        detail = {
            "status": snapshot.get("status", "INVALID_DEPENDENCY"),
            "reason": snapshot.get("reason", "INVALID_DEPENDENCY"),
            "snapshot_id": snapshot.get("snapshot_id", ""),
            "snapshot_trade_date": snapshot.get("trade_date", ""),
        }
        return False, detail
    report = validate_broker_summary(ctx, _expected_symbols_from_snapshot(snapshot))
    detail = {
        **report,
        "snapshot_id": snapshot.get("snapshot_id", ""),
        "snapshot_trade_date": snapshot.get("trade_date", ""),
    }
    return report.get("status") == READY, detail


def wait_for_broker_ready(ctx: RunnerContext) -> tuple[bool, dict[str, Any]]:
    cfg = ctx.scheduler_config.get("final_watchlist", {})
    retry_seconds = int(cfg.get("broker_retry_seconds", int(cfg.get("retry_interval_minutes", 5)) * 60))
    cutoff_text = str(cfg.get("cutoff_time", cfg.get("broker_cutoff_time", "18:30")))
    cutoff_hour, cutoff_minute = [int(x) for x in cutoff_text.split(":", 1)]
    attempts = 0
    last_detail: dict[str, Any] = {}
    while True:
        attempts += 1
        ready, detail = broker_readiness(ctx)
        detail["attempts"] = attempts
        detail["retry_count"] = max(attempts - 1, 0)
        last_detail = detail
        if ready:
            return True, detail
        if detail.get("status") in {"STALE_TECHNICAL_SNAPSHOT", "INVALID_DEPENDENCY"}:
            return False, detail
        current = now_wib()
        cutoff = current.replace(hour=cutoff_hour, minute=cutoff_minute, second=0, microsecond=0)
        if current >= cutoff or ctx.preview_existing:
            last_detail["status"] = "WAITING_DATA_TIMEOUT" if current >= cutoff else detail.get("status", "WAITING_DATA")
            last_detail["reason"] = "BROKER_CUTOFF_REACHED" if current >= cutoff else detail.get("status", "WAITING_DATA")
            return False, last_detail
        append_job_log(ctx, "WAITING_BROKER", str(detail))
        if ctx.dry_run and retry_seconds > 0:
            return False, detail
        time.sleep(retry_seconds)


def run_interactive_broker_break(
    ctx: RunnerContext,
    initial_detail: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Wait for a current-date Tampermonkey export during a manual Final Watchlist run."""
    snapshot = load_technical_snapshot(ctx)
    if snapshot.get("status") != "VALID":
        return False, {
            "status": snapshot.get("status", "INVALID_DEPENDENCY"),
            "reason": snapshot.get("reason", "TECHNICAL_SNAPSHOT_NOT_FOUND"),
            "snapshot_id": snapshot.get("snapshot_id", ""),
            "snapshot_trade_date": snapshot.get("trade_date", ""),
        }

    paths = ctx.config.get("paths", {})
    navigator_text = str(snapshot.get("broker_navigator_path") or "").strip()
    navigator_path = Path(navigator_text) if navigator_text else resolve(
        paths.get("broker_navigator_symbols", "data/output/candidates/BROKER_NAVIGATOR_SYMBOLS.csv")
    )
    if not navigator_path.exists():
        fallback = Path(str(snapshot.get("output_paths", {}).get("broker_symbols", "")))
        navigator_path = fallback if fallback.exists() else navigator_path
    if not navigator_path.exists():
        detail = dict(initial_detail or {})
        detail.update({
            "status": "INVALID_DEPENDENCY",
            "reason": "BROKER_NAVIGATOR_SYMBOLS_NOT_FOUND",
            "broker_navigator_path": str(navigator_path),
        })
        return False, detail

    broker_cfg = ctx.config.get("broker", {})
    manifest_dir = ctx.path("manifest_dir", "data/output/manifests")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    print("", flush=True)
    print("=" * 64, flush=True)
    print("BROKER BREAK - EXPORT BROKER SUMMARY", flush=True)
    print("=" * 64, flush=True)
    print(f"File symbol Tampermonkey : {navigator_path}", flush=True)
    print(f"Tanggal yang dibutuhkan : {ctx.trade_date.isoformat()}", flush=True)
    print("Jalankan Broker Navigator di Stockbit.", flush=True)
    print("Proses akan lanjut otomatis setelah Broker Summary tanggal yang sama ditemukan.", flush=True)
    print("Tekan Ctrl+C bila ingin membatalkan.", flush=True)
    print("=" * 64, flush=True)

    command = [
        sys.executable,
        "-u",
        str(resolve(paths.get("broker_bridge", "modules/broker_bridge/wait_for_broker_export.py"))),
        "--symbols",
        str(navigator_path),
        "--downloads",
        str(broker_cfg.get("downloads_dir", "%USERPROFILE%/Downloads")),
        "--output",
        str(resolve(paths.get("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv"))),
        "--raw-output",
        str(resolve(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))),
        "--timeout",
        str(broker_cfg.get("timeout_seconds", 1200)),
        "--poll",
        str(broker_cfg.get("poll_seconds", 2)),
        "--min-coverage",
        str(broker_cfg.get("min_coverage", 0.8)),
        "--expected-broker-date",
        ctx.trade_date.isoformat(),
        "--technical-date",
        str(snapshot.get("trade_date", ctx.trade_date.isoformat())),
        "--broker-date-policy",
        "exact",
        "--include-existing",
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
    ]
    try:
        run_command(ctx, "MANUAL BROKER BREAK", command)
    except RuntimeError as exc:
        ready, detail = broker_readiness(ctx)
        if ready:
            return True, detail
        detail = {**(initial_detail or {}), **detail}
        detail["broker_break_error"] = str(exc)
        detail["broker_navigator_path"] = str(navigator_path)
        return False, detail

    ready, detail = broker_readiness(ctx)
    detail["broker_navigator_path"] = str(navigator_path)
    return ready, detail


def run_final_from_snapshot(ctx: RunnerContext) -> dict[str, Any]:
    snapshot = load_technical_snapshot(ctx)
    if snapshot.get("status") != "VALID":
        raise RuntimeError(f"Invalid technical snapshot: {snapshot.get('status')} {snapshot.get('reason', '')}")
    cfg = ctx.config
    paths = cfg.get("paths", {})
    manifest_dir = resolve(paths.get("manifest_dir", "data/output/manifests"))
    manifest_dir.mkdir(parents=True, exist_ok=True)
    candidate = Path(snapshot["output_paths"]["technical_candidates"])
    decision_source = resolve(paths.get("decision_source", "data/input/FINAL_DECISION_V2.csv"))
    decision_dir = resolve(paths.get("decision_output_dir", "data/output/decision"))
    exit_dir = resolve(paths.get("exit_output_dir", "data/output/exit"))
    hist = resolve(paths.get("historical_dir", "data/output/historical/by_symbol"))
    ihsg = resolve(paths.get("ihsg_csv", "data/input/IHSG.csv"))
    broker_summary = resolve(paths.get("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv"))
    broker_raw = resolve(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))
    bcfg = cfg.get("broker", {})
    run_manifest_path = manifest_dir / f"SWING_RUN_MANIFEST_{ctx.run_id}.json"
    manifest: dict[str, Any] = {
        "Run_ID": ctx.run_id,
        "Strategy_Type": "SWING",
        "Pipeline_Version": ctx.config_provenance.get("pipeline_version", ""),
        "Config_Source": ctx.config_provenance.get("config_source", str(ctx.config_path)),
        "Config_Hash": ctx.config_provenance.get("config_hash", ""),
        "Config_Version": ctx.config_provenance.get("config_version", ""),
        "Config_Loaded_At": ctx.config_provenance.get("loaded_at", ""),
        "Config_Audit_Path": ctx.config_provenance.get("audit_path", ""),
        "Config_Override_Mode": ctx.config_provenance.get("override_mode", "NONE"),
        "Started_At": ctx.started_at.isoformat(timespec="seconds"),
        "Finished_At": "",
        "Pipeline_Status": "RUNNING",
        "Mode": "SCHEDULED_FINAL_WATCHLIST",
        "Data_Source": "LIVE_SNAPSHOT",
        "Technical_Date": snapshot.get("trade_date", ""),
        "Technical_Snapshot_ID": snapshot.get("snapshot_id", ""),
        "Broker_Date": "",
        "Broker_Coverage": "",
        "Data_Quality_Status": "VALID",
        "Telegram_Status": "SKIPPED",
        "Warnings": [],
        "Errors": [],
        "Source_Files": {"Technical_Candidates": str(candidate), "Broker_Summary": str(broker_summary), "Broker_Raw": str(broker_raw)},
        "Output_Files": {},
    }
    write_json(run_manifest_path, manifest)
    fusion_cmd = [
        sys.executable,
        "-u",
        str(resolve(paths.get("broker_fusion", "modules/broker_fusion/broker_fusion.py"))),
        str(candidate),
        str(broker_summary),
        "--output",
        str(decision_source),
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
        "--data-quality-status",
        "VALID",
        "--min-coverage",
        str(bcfg.get("min_coverage", 0.80)),
        "--expected-broker-date",
        ctx.trade_date.isoformat(),
        "--broker-raw",
        str(broker_raw),
    ]
    run_command(ctx, "BROKER FUSION FROM SNAPSHOT", fusion_cmd)
    fusion_manifest = read_json_safely(manifest_dir / f"BROKER_FUSION_MANIFEST_{ctx.run_id}.json")
    broker_date = str(fusion_manifest.get("broker_date", ""))
    if broker_date != ctx.trade_date.isoformat():
        raise RuntimeError(f"BROKER_DATE_MISMATCH: {broker_date} != {ctx.trade_date.isoformat()}")
    manifest["Broker_Date"] = broker_date
    manifest["Broker_Coverage"] = f"{fusion_manifest.get('broker_matched', 0)}/{fusion_manifest.get('broker_expected', 0)} - {fusion_manifest.get('broker_coverage', 0):.0%}"
    manifest["Fusion_Output"] = str(decision_source)
    manifest["Data_Quality_Status"] = fusion_manifest.get("Data_Quality_Status", "VALID")
    manifest["Output_Files"]["Fusion"] = str(decision_source)
    write_json(run_manifest_path, manifest)
    decision_dir.mkdir(parents=True, exist_ok=True)
    run_command(ctx, "DECISION ENGINE", [
        sys.executable,
        "-u",
        str(resolve(paths.get("decision_engine", "modules/decision_engine/decision_engine.py"))),
        str(decision_source),
        str(decision_dir),
        "--ihsg",
        str(ihsg),
        "--config",
        str(ctx.config_path),
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
        "--data-quality-status",
        str(manifest.get("Data_Quality_Status", "VALID")),
    ])
    final = decision_dir / "FINAL_DECISION_V3.csv"
    manifest["Decision_Output"] = str(final)
    manifest["Output_Files"]["Decision"] = str(final)
    write_json(run_manifest_path, manifest)
    run_command(ctx, "EXIT ENGINE", [
        sys.executable,
        "-u",
        str(resolve(paths.get("exit_engine", "modules/exit_engine/exit_engine.py"))),
        str(final),
        str(hist),
        "--state-file",
        str(resolve(paths.get("active_trades_state", "data/state/ACTIVE_TRADES.csv"))),
        "--output-dir",
        str(exit_dir),
        "--min-rr",
        str(cfg.get("exit", {}).get("min_rr", 1.0)),
        "--preferred-rr",
        str(cfg.get("exit", {}).get("preferred_rr", 2.0)),
        "--max-risk-pct",
        str(cfg.get("exit", {}).get("max_risk_pct", 7.0)),
        "--max-hold-days",
        str(cfg.get("exit", {}).get("max_hold_days", 20)),
        "--run-id",
        ctx.run_id,
        "--manifest-dir",
        str(manifest_dir),
        "--data-quality-status",
        str(manifest.get("Data_Quality_Status", "VALID")),
        "--config",
        str(ctx.config_path),
    ])
    manifest["Exit_Output"] = str(exit_dir)
    manifest["Output_Files"]["Exit"] = str(exit_dir)
    if cfg.get("analytics", {}).get("profile_comparison_enabled", True):
        shadow_output = resolve(paths.get("profile_shadow_output_root", "data/output/profile_shadow")) / ctx.run_id
        run_command(ctx, "MODERATE PROFILE SHADOW", [
            sys.executable,
            "-u",
            str(resolve(paths.get("profile_shadow_runner", "tools/run_moderate_shadow.py"))),
            str(final),
            "--entry-plans",
            str(exit_dir / "ENTRY_PLANS.csv"),
            "--config",
            str(ctx.config_path),
            "--output-dir",
            str(shadow_output),
            "--run-id",
            ctx.run_id,
        ])
        manifest["Output_Files"]["Profile_Shadow"] = str(shadow_output)
        manifest["Shadow_Mode"] = "SHADOW_ONLY"
        manifest["Auto_Entry_Enabled"] = False
    manifest["Finished_At"] = now_wib().isoformat(timespec="seconds")
    manifest["Pipeline_Status"] = "SUCCESS"
    write_json(run_manifest_path, manifest)
    return manifest
