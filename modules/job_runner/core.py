from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from swing_utils import file_sha256, find_col, read_json as read_json_safely, write_json
from modules.data_sources.config import load_data_source_config
from modules.market_data.zapi_enrichment import ZapiEnrichmentService

from .runtime import RunnerContext, append_job_log, now_wib, resolve, stage_watchdog


READY = "READY"


class SourceValidationBlocked(RuntimeError):
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__(
            "ZAPI_SOURCE_VALIDATION_BLOCKED: "
            + str(result.get("reason") or result.get("status") or "UNKNOWN")
        )


def _post_market_evaluation_datetime(ctx: RunnerContext, freshness: dict[str, Any]) -> str:
    """Return the downloader as-of timestamp for this post-market run.

    A live run must use its actual start time so a job launched before the
    IDX close cannot accept the current partial daily candle.  Explicit
    historical/dry-run contexts keep a deterministic close-time timestamp for
    the requested trade date.
    """
    evaluation_at = ctx.started_at
    if evaluation_at.date() == ctx.trade_date:
        return evaluation_at.isoformat(timespec="seconds")
    market_close = str(freshness.get("market_close", "16:15"))
    return f"{ctx.trade_date.isoformat()}T{market_close}:00+07:00"


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


def _universe_symbols(path: Path, historical_dir: Path | None = None) -> list[str]:
    """Return one normalized universe for enrichment and technical stages."""
    symbols: set[str] = set()
    if path.exists() and path.stat().st_size:
        try:
            frame = pd.read_csv(path, low_memory=False)
            column = find_col(frame, "Symbol", "Ticker", "Emiten", "Code")
            if column:
                symbols.update(
                    str(value).strip().upper().replace(".JK", "")
                    for value in frame[column].tolist()
                    if str(value).strip()
                )
        except Exception:
            pass
    if not symbols and historical_dir and historical_dir.exists():
        symbols.update(
            path.stem[:-3] if path.stem.upper().endswith(".JK") else path.stem.upper()
            for path in historical_dir.glob("*.csv")
        )
    return sorted(symbol for symbol in symbols if symbol and symbol not in {"IHSG", "^JKSE"})


def run_zapi_enrichment(
    ctx: RunnerContext,
    *,
    symbols: list[str],
    historical_dir: Path | None = None,
    metadata_csv_path: Path | None = None,
) -> dict[str, Any]:
    """Refresh/read non-blocking Zapi metadata and exchange activity caches."""
    zcfg = ctx.config.get("zapi", {})
    cache_root = ctx.path("zapi_cache_root", "data/state/zapi")

    def zapi_event(event: str, detail: dict[str, Any]) -> None:
        append_job_log(ctx, event, __import__("json").dumps(detail, ensure_ascii=False, default=str))

    try:
        service = getattr(ctx, "_zapi_enrichment_service", None)
        if service is None:
            service = ZapiEnrichmentService(
                config_path=ctx.data_source_config_path,
                cache_root=cache_root,
                event_callback=zapi_event,
                metadata_ttl_days=int(zcfg.get("metadata_ttl_days", 7) or 7),
                max_requests=min(int(zcfg.get("max_requests_per_process", 5) or 5), 5),
                minimum_historical_candles=int(zcfg.get("minimum_historical_candles", 200) or 200),
            )
            setattr(ctx, "_zapi_enrichment_service", service)
        result = service.enrich(
            symbols,
            trade_date=ctx.trade_date,
            historical_dir=historical_dir,
            metadata_csv_path=metadata_csv_path,
            # Full Manual can be repeated during the same trading day.  The
            # persistent date/TTL caches are authoritative for this optional
            # source, so a generic pipeline force flag must not create another
            # Zapi refresh.
            force=False,
        )
        append_job_log(
            ctx,
            "ZAPI_RUNTIME_REPORT",
            __import__("json").dumps(
                {
                    key: result.get(key)
                    for key in (
                        "request_count", "request_cap", "metadata_cache_status", "metadata_cache_date",
                        "market_activity_cache_status", "suspended_count", "uma_count", "relisting_count",
                        "degraded", "degraded_reason",
                    )
                },
                ensure_ascii=False,
                default=str,
            ),
        )
        return result
    except Exception as exc:
        detail = {
            "status": "DEGRADED",
            "source": "ZAPI_IDX",
            "degraded": True,
            "degraded_reason": f"{type(exc).__name__}: {exc}",
            "trade_date": ctx.trade_date.isoformat(),
            "request_count": 0,
            "request_cap": min(int(zcfg.get("max_requests_per_process", 5) or 5), 5),
            "metadata_cache_status": "UNAVAILABLE",
            "metadata_cache_date": "",
            "market_activity_cache_status": "UNAVAILABLE",
            "suspended_symbols": [],
            "uma_symbols": [],
            "relisting_symbols": [],
            "suspended_count": 0,
            "uma_count": 0,
            "relisting_count": 0,
            "symbols": {},
        }
        # Keep the degraded result addressable by the downstream candidate and
        # final stages.  Use the same stage-specific manifest root as the
        # normal post-market path, including dry-run isolation.
        try:
            fallback_path = _stage_paths(ctx)["manifest_dir"] / f"ZAPI_ENRICHMENT_{ctx.run_id}.json"
            detail["path"] = str(fallback_path)
            write_json(fallback_path, detail)
        except Exception:
            # The original exception is the useful runtime fact; inability to
            # persist its fallback artifact must not turn optional Zapi into a
            # hard pipeline failure.
            pass
        append_job_log(ctx, "ZAPI_ENRICHMENT_DEGRADED", detail["degraded_reason"])
        return detail


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
    # Evaluate freshness at the moment this run started.  The previous
    # implementation hard-coded 17:00 for every run, which made a job
    # started before the IDX close treat the current day's in-progress Yahoo
    # candle as closed.  That partial value then became "already current" and
    # could remain frozen in performance artifacts.  Keep an explicit close
    # time only for historical/dry-run contexts whose trade date is not the
    # local date of the run.
    evaluation_dt = _post_market_evaluation_datetime(ctx, freshness)
    append_job_log(ctx, "POST_MARKET_EVALUATION_DATETIME", evaluation_dt)
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
        "--repair-overlap-sessions",
        str(freshness.get("yahoo_repair_overlap_sessions", 5)),
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
    for special_day in freshness.get("special_trading_days", []):
        downloader_cmd += ["--special-trading-day", str(special_day)]
    if bool(getattr(ctx, "reuse_yahoo_refresh", False)):
        candidates = sorted(
            manifest_dir.glob("YAHOO_REFRESH_MANIFEST_*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError("YAHOO_REFRESH_MANIFEST_NOT_FOUND_FOR_REUSE")
        yahoo_manifest_path = candidates[0]
        yahoo_manifest = read_json_safely(yahoo_manifest_path)
        if str(yahoo_manifest.get("Data_Quality_Status", "")).upper() != "VALID":
            raise RuntimeError("YAHOO_REFRESH_MANIFEST_NOT_VALID_FOR_REUSE")
        append_job_log(
            ctx,
            "COMMAND_OK",
            f"POST MARKET HISTORICAL DOWNLOADER (REUSED_EXISTING): {yahoo_manifest_path}",
        )
    else:
        run_command(ctx, "POST MARKET HISTORICAL DOWNLOADER", downloader_cmd)
        yahoo_manifest = read_json_safely(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{ctx.run_id}.json")
    data_quality = str(yahoo_manifest.get("Data_Quality_Status", "VALID"))
    # Zapi is an optional enrichment layer.  It is deliberately called after
    # Yahoo technical data has been produced and is never allowed to veto that
    # pipeline.  In particular, this replaces the old mass Yahoo-vs-Zapi
    # /stock-summary reconciliation path.
    universe_path = resolve(paths.get("normalized_watchlist", "modules/historical_downloader/Stockbit_Watchlist_2026-07-19_normalized.csv"))
    symbols = _universe_symbols(universe_path, stage_paths["historical_by_symbol"])
    zapi_enrichment = run_zapi_enrichment(
        ctx,
        symbols=symbols,
        historical_dir=stage_paths["historical_by_symbol"],
        metadata_csv_path=resolve(paths.get("sector_rotation_metadata", "data/input/sector_metadata.csv")),
    )
    zapi_status_path = Path(str(zapi_enrichment.get("path") or stage_paths["manifest_dir"] / f"ZAPI_ENRICHMENT_{ctx.run_id}.json"))
    if not zapi_status_path.exists():
        write_json(zapi_status_path, zapi_enrichment)
    if zapi_enrichment.get("degraded"):
        data_quality = data_quality if data_quality != "VALID" else "VALID_WITH_ZAPI_WARNING"
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
        "--exchange-status",
        str(zapi_status_path),
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
        reconciliation=zapi_enrichment,
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
        # The snapshot is the Stage-1/2 source of truth.  Do not report the
        # DataSourceManager readiness metadata as if it described the Yahoo
        # files that were actually consumed by the technical engine.
        "provider_status": snapshot.get("source_metadata", {}).get("provider_status") or source_metadata.get("provider_status", ""),
        "data_source_mode": snapshot.get("source_metadata", {}).get("data_source_mode") or source_metadata.get("data_source_mode", ""),
        "source_coverage_ratio": snapshot.get("source_metadata", {}).get("source_coverage_ratio"),
        "Reconciliation_Status": zapi_enrichment.get("status", ""),
        "Reconciliation_Manifest": zapi_enrichment.get("path", ""),
        "reconciliation": {key: value for key, value in zapi_enrichment.items() if key != "symbols"},
        "snapshot_ids": {"technical": snapshot.get("snapshot_id", "")},
        "Config_Version": ctx.runtime_version,
        "Broker_Navigator_Path": str(navigator_path),
        "Warnings": [
            x for x in [
                yahoo_manifest.get("Warning"),
                candidate_manifest.get("Reason"),
                "ZAPI_DEGRADED; technical Yahoo pipeline tetap berjalan" if zapi_enrichment.get("degraded") else "",
            ] if x
        ],
        "Zapi_Request_Count": zapi_enrichment.get("request_count", 0),
        "Zapi_Request_Cap": zapi_enrichment.get("request_cap", 5),
        "Zapi_Metadata_Cache_Status": zapi_enrichment.get("metadata_cache_status", ""),
        "Zapi_Metadata_Cache_Date": zapi_enrichment.get("metadata_cache_date", ""),
        "Zapi_Market_Activity_Cache_Status": zapi_enrichment.get("market_activity_cache_status", ""),
        "Suspended_Symbol_Count": zapi_enrichment.get("suspended_count", 0),
        "Uma_Symbol_Count": zapi_enrichment.get("uma_count", 0),
        "Relisting_Symbol_Count": zapi_enrichment.get("relisting_count", 0),
        "Zapi_Degraded": bool(zapi_enrichment.get("degraded")),
        "Zapi_Enrichment_Path": zapi_enrichment.get("path", ""),
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
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reconciliation = reconciliation or {}
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
            "source_coverage_ratio": round((symbols_valid / symbols_requested), 4) if symbols_requested else 0.0,
            "historical_source": "YAHOO",
            "latest_validation_source": "NONE; ZAPI enrichment only",
            "zapi_status": reconciliation.get("status", "ZAPI_DISABLED"),
            # Metadata record count is not a technical coverage ratio.  Keep
            # the field at zero unless an explicit coverage metric exists.
            "zapi_coverage_ratio": reconciliation.get("coverage_ratio", 0.0),
            "reconciliation_status": reconciliation.get("status", "ZAPI_DISABLED"),
            "degraded_reason": reconciliation.get("degraded_reason", reconciliation.get("reason", "")),
            "zapi_enrichment_path": reconciliation.get("path", ""),
            "zapi_request_count": reconciliation.get("request_count", 0),
            "zapi_request_cap": reconciliation.get("request_cap", 5),
            "metadata_cache_status": reconciliation.get("metadata_cache_status", ""),
            "metadata_cache_date": reconciliation.get("metadata_cache_date", ""),
            "market_activity_cache_status": reconciliation.get("market_activity_cache_status", ""),
            "suspended_count": reconciliation.get("suspended_count", 0),
            "uma_count": reconciliation.get("uma_count", 0),
            "relisting_count": reconciliation.get("relisting_count", 0),
            "zapi_degraded": bool(reconciliation.get("degraded")),
        },
        "reconciliation": {key: value for key, value in reconciliation.items() if key != "symbols"},
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

    command = _broker_bridge_command(
        ctx,
        snapshot,
        navigator_path,
        timeout_seconds=int(broker_cfg.get("timeout_seconds", 1200)),
        poll_seconds=float(broker_cfg.get("poll_seconds", 2)),
        manifest_dir=manifest_dir,
    )
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


def _broker_bridge_command(
    ctx: RunnerContext,
    snapshot: dict[str, Any],
    navigator_path: Path,
    *,
    timeout_seconds: int,
    poll_seconds: float,
    manifest_dir: Path | None = None,
) -> list[str]:
    """Build the broker export bridge command used by manual and auto-import flows."""

    paths = ctx.config.get("paths", {})
    broker_cfg = ctx.config.get("broker", {})
    manifest_dir = manifest_dir or ctx.path("manifest_dir", "data/output/manifests")
    return [
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
        str(max(0, int(timeout_seconds))),
        "--poll",
        str(max(0.1, float(poll_seconds))),
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


def try_import_existing_broker_export(ctx: RunnerContext) -> tuple[bool, dict[str, Any]]:
    """Import a current-date broker export already present in Downloads.

    The standalone ``broker_summary`` menu must be able to consume the same
    Downloads artifact as Final Watchlist.  This helper is intentionally
    non-blocking: it performs one scan only and never waits for a future
    Tampermonkey export.
    """

    ready, detail = broker_readiness(ctx)
    if ready:
        detail["broker_import_source"] = "LOCAL_INPUT"
        return True, detail

    snapshot = load_technical_snapshot(ctx)
    if snapshot.get("status") != "VALID":
        return False, {
            **detail,
            "status": snapshot.get("status", "INVALID_DEPENDENCY"),
            "reason": snapshot.get("reason", "TECHNICAL_SNAPSHOT_NOT_FOUND"),
            "snapshot_id": snapshot.get("snapshot_id", ""),
            "snapshot_trade_date": snapshot.get("trade_date", ""),
        }

    broker_cfg = ctx.config.get("broker", {})
    downloads = Path(os.path.expandvars(str(broker_cfg.get("downloads_dir", "%USERPROFILE%/Downloads")))).expanduser()
    if not downloads.exists():
        return False, {
            **detail,
            "status": "FILE_NOT_FOUND",
            "reason": "BROKER_DOWNLOADS_NOT_FOUND",
            "downloads": str(downloads),
            "broker_import_source": "DOWNLOADS_AUTO_IMPORT",
        }
    candidates = list(downloads.glob("BROKER_SUMMARY_COMBINED_*.csv"))
    if not candidates:
        return False, {
            **detail,
            "status": "FILE_NOT_FOUND",
            "reason": "BROKER_EXPORT_NOT_FOUND_FOR_CURRENT_DATE",
            "downloads": str(downloads),
            "broker_import_source": "DOWNLOADS_AUTO_IMPORT",
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
        return False, {
            **detail,
            "status": "INVALID_DEPENDENCY",
            "reason": "BROKER_NAVIGATOR_SYMBOLS_NOT_FOUND",
            "broker_navigator_path": str(navigator_path),
            "broker_import_source": "DOWNLOADS_AUTO_IMPORT",
        }

    manifest_dir = ctx.path("manifest_dir", "data/output/manifests")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    append_job_log(ctx, "BROKER_AUTO_IMPORT_START", json.dumps({
        "downloads": str(downloads),
        "candidate_count": len(candidates),
        "expected_date": ctx.trade_date.isoformat(),
        # The bridge performs its first scan inside a time-bounded loop.  One
        # second guarantees that scan executes while remaining non-blocking
        # for the standalone menu.
        "timeout_seconds": 1,
    }))
    command = _broker_bridge_command(
        ctx,
        snapshot,
        navigator_path,
        timeout_seconds=1,
        poll_seconds=0.1,
        manifest_dir=manifest_dir,
    )
    try:
        run_command(ctx, "BROKER AUTO-IMPORT", command)
    except RuntimeError as exc:
        _, after = broker_readiness(ctx)
        after.update({
            "broker_import_source": "DOWNLOADS_AUTO_IMPORT",
            "broker_auto_import_error": str(exc),
        })
        append_job_log(ctx, "BROKER_AUTO_IMPORT_NOT_READY", str(after))
        return False, after

    ready, after = broker_readiness(ctx)
    after["broker_import_source"] = "DOWNLOADS_AUTO_IMPORT"
    append_job_log(ctx, "BROKER_AUTO_IMPORT_COMPLETE", str(after))
    return ready, after


def run_broker_fusion_from_snapshot(
    ctx: RunnerContext,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the single Broker Fusion stage used by summary and final jobs.

    Broker Fusion is the owner of broker score/confirmation fields.  Keeping
    this command in one helper prevents the report bridge from re-deriving
    broker state from the raw Stockbit export.
    """
    snapshot = snapshot or load_technical_snapshot(ctx)
    if snapshot.get("status") != "VALID":
        raise RuntimeError(f"Invalid technical snapshot: {snapshot.get('status')} {snapshot.get('reason', '')}")
    cfg = ctx.config
    paths = cfg.get("paths", {})
    manifest_dir = resolve(paths.get("manifest_dir", "data/output/manifests"))
    manifest_dir.mkdir(parents=True, exist_ok=True)
    candidate = Path(str(snapshot.get("output_paths", {}).get("technical_candidates", "")))
    decision_source = resolve(paths.get("decision_source", "data/input/FINAL_DECISION_V2.csv"))
    broker_summary = resolve(paths.get("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv"))
    broker_raw = resolve(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))
    bcfg = cfg.get("broker", {})
    fusion_manifest_path = manifest_dir / f"BROKER_FUSION_MANIFEST_{ctx.run_id}.json"
    existing_manifest = read_json_safely(fusion_manifest_path)
    if ctx.preview_existing and decision_source.exists():
        # Preview mode may reuse a canonical artifact produced by an earlier
        # run.  The caller validates date/config/source before allowing this
        # path; the engine run ID does not become a reason to re-fuse data.
        for candidate_manifest_path in sorted(
            manifest_dir.glob("BROKER_FUSION_MANIFEST_*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            candidate_manifest = read_json_safely(candidate_manifest_path)
            if str(candidate_manifest.get("broker_date", "")) != ctx.trade_date.isoformat():
                continue
            if str(candidate_manifest.get("output", "")) and Path(str(candidate_manifest.get("output"))).resolve() != decision_source.resolve():
                continue
            return {
                "candidate": candidate,
                "decision_source": decision_source,
                "broker_summary": broker_summary,
                "broker_raw": broker_raw,
                "manifest_dir": candidate_manifest_path.parent,
                "fusion_manifest": candidate_manifest,
                "fusion_manifest_path": str(candidate_manifest_path),
                "reused": True,
            }
    if (
        decision_source.exists()
        and str(existing_manifest.get("broker_date", "")) == ctx.trade_date.isoformat()
    ):
        return {
            "candidate": candidate,
            "decision_source": decision_source,
            "broker_summary": broker_summary,
            "broker_raw": broker_raw,
            "manifest_dir": manifest_dir,
            "fusion_manifest": existing_manifest,
            "fusion_manifest_path": str(fusion_manifest_path),
            "reused": True,
        }
    if ctx.job != "broker_summary":
        broker_status = read_json_safely(ctx.status_root / "broker_summary_latest.json")
        status_paths = broker_status.get("output_paths", {}) if isinstance(broker_status, dict) else {}
        dependency_manifest_raw = str(status_paths.get("broker_fusion_manifest", "")).strip()
        dependency_manifest_path = Path(dependency_manifest_raw) if dependency_manifest_raw else None
        dependency_manifest = read_json_safely(dependency_manifest_path) if dependency_manifest_path else {}
        if (
            decision_source.exists()
            and str(dependency_manifest.get("broker_date", "")) == ctx.trade_date.isoformat()
        ):
            return {
                "candidate": candidate,
                "decision_source": decision_source,
                "broker_summary": broker_summary,
                "broker_raw": broker_raw,
                "manifest_dir": dependency_manifest_path.parent if dependency_manifest_path else manifest_dir,
                "fusion_manifest": dependency_manifest,
                "fusion_manifest_path": str(dependency_manifest_path) if dependency_manifest_path else "",
                "reused": True,
            }
    command = [
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
        str(snapshot.get("data_quality_status") or "VALID"),
        "--min-coverage",
        str(bcfg.get("min_coverage", 0.80)),
        "--expected-broker-date",
        ctx.trade_date.isoformat(),
        "--broker-raw",
        str(broker_raw),
    ]
    if bool(bcfg.get("allow_partial_broker", False)):
        command.append("--allow-partial-broker")
    run_command(ctx, "BROKER FUSION FROM SNAPSHOT", command)
    fusion_manifest = read_json_safely(fusion_manifest_path)
    broker_date = str(fusion_manifest.get("broker_date", ""))
    if broker_date != ctx.trade_date.isoformat():
        raise RuntimeError(f"BROKER_DATE_MISMATCH: {broker_date} != {ctx.trade_date.isoformat()}")
    return {
        "candidate": candidate,
        "decision_source": decision_source,
        "broker_summary": broker_summary,
        "broker_raw": broker_raw,
        "manifest_dir": manifest_dir,
        "fusion_manifest": fusion_manifest,
        "fusion_manifest_path": str(fusion_manifest_path),
        "reused": False,
    }


def run_broker_multiday_stage(ctx: RunnerContext) -> dict[str, Any]:
    """Build the published broker multi-day context files from dated raw exports.

    This activates the existing multi-day engine/output writer; it does not
    calculate or alter BUY/WATCH/AVOID decisions.
    """
    from modules.data_sources.broker_multiday_output import write_multiday_outputs
    from modules.data_sources.decision_bridge import build_contexts_for_symbols

    paths = ctx.config.get("paths", {})
    latest = resolve(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))
    archive_dir = resolve(paths.get("broker_raw_archive_dir", "data/input/broker/archive"))
    candidates = [path for path in [latest, *sorted(archive_dir.glob("BROKER_RAW_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)] if path.exists() and path.stat().st_size > 0]
    if not candidates:
        raise RuntimeError("BROKER_MULTI_DAY_RAW_SOURCE_NOT_FOUND")

    # Pick the newest complete capture for each market date.  Multiple browser
    # captures of one date must never be merged as if they were separate days.
    selected: dict[str, tuple[Path, pd.DataFrame]] = {}
    for path in candidates:
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        date_col = find_col(frame, "TO_DATE", "Market_Date", "Date")
        if date_col is None:
            continue
        dates = pd.to_datetime(frame[date_col], errors="coerce").dropna().dt.date.astype(str)
        dates = [item for item in dates if item <= ctx.trade_date.isoformat()]
        if not dates:
            continue
        market_date = max(dates)
        if market_date not in selected:
            selected[market_date] = (path, frame)

    if not selected:
        raise RuntimeError("BROKER_MULTI_DAY_MARKET_DATE_NOT_FOUND")

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for _market_date, (_path, frame) in sorted(selected.items()):
        symbol_col = find_col(frame, "SYMBOL", "EMITEN", "Ticker", "Symbol")
        date_col = find_col(frame, "TO_DATE", "Market_Date", "Date")
        if symbol_col is None or date_col is None:
            continue
        parsed_dates = pd.to_datetime(frame[date_col], errors="coerce").dt.date.astype(str)
        frame = frame.loc[parsed_dates.eq(_market_date)].copy()
        for raw in frame.to_dict(orient="records"):
            symbol = str(raw.get(symbol_col, "")).strip().upper().replace(".JK", "")
            parsed_market_date = pd.to_datetime(raw.get(date_col), errors="coerce")
            if pd.isna(parsed_market_date):
                continue
            market_date = parsed_market_date.date().isoformat()
            if not symbol or market_date > ctx.trade_date.isoformat():
                continue
            def number(*aliases: str) -> float | None:
                column = find_col(frame, *aliases)
                try:
                    return float(raw.get(column)) if column and raw.get(column) not in (None, "") else None
                except (TypeError, ValueError):
                    return None
            row = {
                "symbol": symbol,
                "market_date": market_date,
                "broker_code": str(raw.get(find_col(frame, "BROKER_CODE", "Broker_Code") or "", "")).upper(),
                "broker_type": str(raw.get(find_col(frame, "BROKER_TYPE", "Broker_Type") or "", "UNKNOWN")).upper(),
                "side": str(raw.get(find_col(frame, "SIDE", "Side") or "", "")).upper(),
                "net_value": number("NET_VALUE", "Net_Value"),
                "net_lot": number("NET_LOT", "Net_Lot"),
                "gross_value": number("GROSS_VALUE", "Gross_Value"),
                "gross_lot": number("GROSS_LOT", "Gross_Lot"),
                "frequency": number("FREQUENCY", "Frequency"),
                "avg_price": number("AVG_PRICE", "Avg_Price"),
                "rank": number("RANK", "Rank"),
            }
            rows_by_symbol.setdefault(symbol, []).append(row)

    if not rows_by_symbol:
        raise RuntimeError("BROKER_MULTI_DAY_ROWS_EMPTY")
    contexts = build_contexts_for_symbols(
        rows_by_symbol,
        primary_window=str(ctx.config.get("broker", {}).get("primary_window", "5D")),
        as_of_date=ctx.trade_date.isoformat(),
    )
    output_dir = resolve(paths.get("broker_multiday_output_dir", "data/output/broker_multiday"))
    minimum_sessions = int(ctx.config.get("broker", {}).get("minimum_multiday_sessions", 20))
    data_quality_status = "VALID" if len(selected) >= minimum_sessions else "INSUFFICIENT_HISTORY"
    outputs = write_multiday_outputs(
        contexts,
        output_dir=output_dir,
        run_id=ctx.run_id,
        data_quality_status=data_quality_status,
        market_dates=sorted(selected),
        source_files=[str(path) for path, _frame in selected.values()],
        minimum_sessions=minimum_sessions,
    )
    from modules.job_runner.report_validation import ReportSourceValidationError

    # A short archive is a truthful partial/insufficient-history result.  It
    # must remain publishable for audit, but it must not be attached to the
    # decision source or used by the final decision stage.
    if data_quality_status != "VALID":
        return {
            "output_paths": {name: str(path) for name, path in outputs.items()},
            "symbol_count": len(contexts),
            "market_dates": sorted(selected),
            "source_files": [str(path) for path, _frame in selected.values()],
            "data_quality_status": data_quality_status,
            "minimum_sessions": minimum_sessions,
            "coverage_ratio": round(len(selected) / max(minimum_sessions, 1), 4),
            "decision_context_path": "",
            "decision_context_symbols": 0,
            "context_bridge": {
                "status": "SKIPPED_INSUFFICIENT_HISTORY",
                "reason": f"{len(selected)} sessions available; {minimum_sessions} required",
            },
        }

    try:
        context_bridge = attach_broker_multiday_context(
            ctx,
            fusion_path=ctx.path("broker_summary_engine", "data/input/FINAL_DECISION_V2.csv"),
        )
    except ReportSourceValidationError:
        raise
    except Exception as exc:
        raise ReportSourceValidationError(
            "broker_multi_day",
            [f"CONTEXT_BRIDGE_FAILED:{type(exc).__name__}:{exc}"],
            input_paths=[
                output_dir / "BROKER_MULTIDAY_MANIFEST.json",
                output_dir / "BROKER_MULTIDAY_SUMMARY.csv",
                output_dir / "BROKER_MULTIDAY_DETAIL.csv",
                ctx.path("broker_summary_engine", "data/input/FINAL_DECISION_V2.csv"),
            ],
            source_of_truth=[output_dir / "BROKER_MULTIDAY_DETAIL.csv", ctx.path("broker_summary_engine", "data/input/FINAL_DECISION_V2.csv")],
        ) from exc
    return {
        "output_paths": {name: str(path) for name, path in outputs.items()},
        "symbol_count": len(contexts),
        "market_dates": sorted(selected),
        "source_files": [str(path) for path, _frame in selected.values()],
        "data_quality_status": data_quality_status,
        "minimum_sessions": minimum_sessions,
        "coverage_ratio": round(len(selected) / max(minimum_sessions, 1), 4),
        "decision_context_path": context_bridge["fusion_path"],
        "decision_context_symbols": context_bridge["symbols_attached"],
    }


def attach_broker_multiday_context(
    ctx: RunnerContext,
    *,
    fusion_path: Path | None = None,
) -> dict[str, Any]:
    """Attach published multi-day context to the Broker Fusion input.

    The bridge adds only the context columns declared by ``decision_bridge``;
    protected decision/status/score columns are checked before and after the
    write.  Decision Engine can therefore consume the existing context
    contract without a second scoring implementation.
    """
    from modules.data_sources.decision_bridge import CONTEXT_COLUMNS
    from modules.job_runner.report_validation import validate_broker_multiday_source

    output_dir = resolve(ctx.config.get("paths", {}).get("broker_multiday_output_dir", "data/output/broker_multiday"))
    manifest_path = output_dir / "BROKER_MULTIDAY_MANIFEST.json"
    multiday_manifest = read_json_safely(manifest_path)
    expected_run_ids = {ctx.run_id}
    # The multi-day producer enriches the Broker Fusion file emitted by the
    # preceding broker-summary job.  Individual scheduled jobs have distinct
    # run IDs, while Full Manual keeps one run ID across both stages.
    dependency_job = "broker_summary" if ctx.job == "broker_multi_day" else "broker_multi_day"
    dependency_status = read_json_safely(ctx.status_root / f"{dependency_job}_latest.json")
    if isinstance(dependency_status, dict) and dependency_status.get("run_id"):
        expected_run_ids.add(str(dependency_status["run_id"]))
    manifest_run_id = str(multiday_manifest.get("run_id", ""))
    if manifest_run_id not in expected_run_ids:
        raise RuntimeError(f"BROKER_MULTI_DAY_RUN_MISMATCH: {manifest_run_id} not in {sorted(expected_run_ids)}")
    if str(multiday_manifest.get("data_quality_status", "")).upper() != "VALID":
        raise RuntimeError(f"BROKER_MULTI_DAY_DATA_QUALITY:{multiday_manifest.get('data_quality_status', '')}")

    summary_path = output_dir / "BROKER_MULTIDAY_SUMMARY.csv"
    detail_path = output_dir / "BROKER_MULTIDAY_DETAIL.csv"
    summary = pd.read_csv(summary_path, low_memory=False)
    detail = pd.read_csv(detail_path, low_memory=False)
    validate_broker_multiday_source(summary, summary_path)
    validate_broker_multiday_source(detail, detail_path)
    fusion = resolve(fusion_path or ctx.path("broker_summary_engine", "data/input/FINAL_DECISION_V2.csv"))
    frame = pd.read_csv(fusion, low_memory=False)

    fusion_symbol = find_col(frame, "Symbol", "EMITEN", "Ticker")
    summary_symbol = find_col(summary, "Symbol", "EMITEN", "Ticker")
    detail_symbol = find_col(detail, "Symbol", "EMITEN", "Ticker")
    if fusion_symbol is None or summary_symbol is None or detail_symbol is None:
        raise RuntimeError("BROKER_MULTI_DAY_CONTEXT_SYMBOL_COLUMN_MISSING")

    def canonical(value: Any) -> str:
        return str(value or "").strip().upper().replace(".JK", "")

    summary_map = {canonical(row.get(summary_symbol)): row for row in summary.to_dict(orient="records")}
    detail_map = {canonical(row.get(detail_symbol)): row for row in detail.to_dict(orient="records")}
    symbols = [canonical(value) for value in frame[fusion_symbol].tolist()]
    missing = sorted(symbol for symbol in symbols if symbol and symbol not in detail_map)
    if missing:
        raise RuntimeError(f"BROKER_MULTI_DAY_CONTEXT_MISSING:{','.join(missing)}")
    missing_summary = sorted(symbol for symbol in symbols if symbol and symbol not in summary_map)
    if missing_summary:
        raise RuntimeError(f"BROKER_MULTI_DAY_SUMMARY_MISSING:{','.join(missing_summary)}")

    def source_value(symbol: str, column: str, *aliases: str) -> Any:
        detail_row = detail_map.get(symbol, {})
        summary_row = summary_map.get(symbol, {})
        for row in (detail_row, summary_row):
            for alias in (column, *aliases):
                if alias in row and row[alias] not in (None, ""):
                    return row[alias]
        return ""

    aliases: dict[str, tuple[str, ...]] = {
        "Broker_MultiDay_Score": ("Score",),
        "Broker_MultiDay_Confidence": ("Confidence",),
        "Broker_MultiDay_Penalty": ("Penalty",),
        "Broker_MultiDay_Blocker": ("Blocker",),
        "Broker_MultiDay_Context": ("Context",),
    }
    protected = {
        column: frame[column].copy()
        for column in ("Decision_Status_Final", "Decision_Status", "Decision_V3", "Final_Score_V3")
        if column in frame.columns
    }
    for column in CONTEXT_COLUMNS:
        frame[column] = [source_value(symbol, column, *aliases.get(column, ())) for symbol in symbols]
    for column, before in protected.items():
        if not frame[column].equals(before):
            raise RuntimeError(f"BROKER_MULTI_DAY_PROTECTED_COLUMN_CHANGED:{column}")
    frame.to_csv(fusion, index=False, encoding="utf-8-sig")
    bridge_metadata = {
        "manifest": str(manifest_path),
        "columns": list(CONTEXT_COLUMNS),
        "symbols_attached": len([symbol for symbol in symbols if symbol]),
    }
    fusion_artifact_manifest = fusion.with_suffix(".manifest.json")
    manifest_candidates = {fusion_artifact_manifest}
    fusion_run_id = str(read_json_safely(fusion_artifact_manifest).get("Run_ID", ""))
    if fusion_run_id:
        manifest_candidates.add(
            resolve(ctx.config.get("paths", {}).get("manifest_dir", "data/output/manifests"))
            / f"BROKER_FUSION_MANIFEST_{fusion_run_id}.json"
        )
    for manifest_candidate in manifest_candidates:
        fusion_manifest_payload = read_json_safely(manifest_candidate)
        if not fusion_manifest_payload:
            continue
        fusion_manifest_payload["output_hash"] = file_sha256(fusion)
        fusion_manifest_payload["multi_day_context_bridge"] = bridge_metadata
        write_json(manifest_candidate, fusion_manifest_payload)
    return {
        "fusion_path": str(fusion),
        "symbols_attached": len([symbol for symbol in symbols if symbol]),
        "manifest_path": str(manifest_path),
        "context_columns": list(CONTEXT_COLUMNS),
    }


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
        "Snapshot_ID": snapshot.get("snapshot_id", ""),
        "Snapshot_Manifest": snapshot.get("manifest_path", ""),
        "source_metadata": snapshot.get("source_metadata", {}),
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
    fusion = run_broker_fusion_from_snapshot(ctx, snapshot)
    fusion_manifest = fusion["fusion_manifest"]
    fusion_manifest_path = Path(str(fusion.get("fusion_manifest_path", manifest_dir / f"BROKER_FUSION_MANIFEST_{ctx.run_id}.json")))
    broker_date = str(fusion_manifest.get("broker_date", ""))
    manifest["Broker_Date"] = broker_date
    manifest["Broker_Coverage"] = f"{fusion_manifest.get('broker_matched', 0)}/{fusion_manifest.get('broker_expected', 0)} - {fusion_manifest.get('broker_coverage', 0):.0%}"
    manifest["Fusion_Output"] = str(decision_source)
    manifest["Data_Quality_Status"] = fusion_manifest.get("Data_Quality_Status", "VALID")
    manifest["Output_Files"]["Fusion"] = str(decision_source)
    write_json(run_manifest_path, manifest)
    context_bridge: dict[str, Any] = {}
    if str(ctx.config_provenance.get("config_version", "")) == "1.7.0-multisource":
        from modules.data_sources.decision_bridge import CONTEXT_COLUMNS

        output_dir = resolve(paths.get("broker_multiday_output_dir", "data/output/broker_multiday"))
        multiday_manifest_path = output_dir / "BROKER_MULTIDAY_MANIFEST.json"
        multiday_manifest = read_json_safely(multiday_manifest_path)
        multiday_quality = str(multiday_manifest.get("data_quality_status", "")).upper()
        if multiday_quality == "VALID":
            from modules.job_runner.report_validation import ReportSourceValidationError

            try:
                context_bridge = attach_broker_multiday_context(ctx, fusion_path=decision_source)
            except ReportSourceValidationError:
                raise
            except Exception as exc:
                raise ReportSourceValidationError(
                    "final_watchlist",
                    [f"CONTEXT_BRIDGE_FAILED:{type(exc).__name__}:{exc}"],
                    input_paths=[
                        multiday_manifest_path,
                        output_dir / "BROKER_MULTIDAY_SUMMARY.csv",
                        output_dir / "BROKER_MULTIDAY_DETAIL.csv",
                        decision_source,
                    ],
                    source_of_truth=[output_dir / "BROKER_MULTIDAY_DETAIL.csv", decision_source],
                ) from exc
        else:
            # Clear stale context from a reused fusion artifact.  Protected
            # decision/status/score columns are untouched, and the decision
            # engine proceeds using only valid one-day broker fusion data.
            if decision_source.exists():
                fusion_frame = pd.read_csv(decision_source, low_memory=False)
                for column in CONTEXT_COLUMNS:
                    if column in fusion_frame.columns:
                        fusion_frame[column] = ""
                fusion_frame.to_csv(decision_source, index=False, encoding="utf-8-sig")
            context_bridge = {
                "status": "SKIPPED_INSUFFICIENT_HISTORY" if multiday_quality else "SKIPPED_NOT_AVAILABLE",
                "reason": str(multiday_manifest.get("data_quality_status") or "BROKER_MULTIDAY_MANIFEST_NOT_FOUND"),
                "manifest": str(multiday_manifest_path),
                "minimum_sessions": multiday_manifest.get("minimum_sessions", 20),
                "session_count": multiday_manifest.get("session_count", 0),
            }
        manifest["Decision_Context_Bridge"] = context_bridge
        if context_bridge.get("fusion_path"):
            manifest["Output_Files"]["Decision_Context"] = context_bridge["fusion_path"]
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
    from modules.decision.exchange_status import apply_exchange_status_to_decisions

    enrichment_path = (
        snapshot.get("source_metadata", {}).get("zapi_enrichment_path")
        or ctx.path("zapi_enrichment_latest", "data/state/zapi/enrichment_latest.json")
    )
    exchange_result = apply_exchange_status_to_decisions(final, enrichment_path)
    manifest["Exchange_Status"] = exchange_result
    manifest["Zapi_Request_Count"] = snapshot.get("source_metadata", {}).get("zapi_request_count", 0)
    manifest["Zapi_Request_Cap"] = snapshot.get("source_metadata", {}).get("zapi_request_cap", 5)
    manifest["Zapi_Metadata_Cache_Status"] = snapshot.get("source_metadata", {}).get("metadata_cache_status", "")
    manifest["Zapi_Metadata_Cache_Date"] = snapshot.get("source_metadata", {}).get("metadata_cache_date", "")
    manifest["Zapi_Market_Activity_Cache_Status"] = snapshot.get("source_metadata", {}).get("market_activity_cache_status", "")
    manifest["Suspended_Symbol_Count"] = exchange_result.get("suspended_count", snapshot.get("source_metadata", {}).get("suspended_count", 0))
    manifest["Uma_Symbol_Count"] = exchange_result.get("uma_count", snapshot.get("source_metadata", {}).get("uma_count", 0))
    manifest["Relisting_Symbol_Count"] = snapshot.get("source_metadata", {}).get("relisting_count", 0)
    manifest["Zapi_Degraded"] = bool(snapshot.get("source_metadata", {}).get("zapi_degraded", False))
    if manifest["Zapi_Degraded"]:
        manifest.setdefault("Warnings", []).append("ZAPI_DEGRADED; technical and broker pipeline continued")
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
    if cfg.get("database", {}).get("enabled", True):
        database_path = resolve(paths.get("swing_database", "data/database/sde_swing_history.db"))
        database_summary = manifest_dir / f"DATABASE_ARCHIVE_SUMMARY_{ctx.run_id}.json"
        manifest["Finished_At"] = now_wib().isoformat(timespec="seconds")
        manifest["Pipeline_Status"] = "SUCCESS"
        write_json(run_manifest_path, manifest)
        try:
            run_command(ctx, "DATABASE ARCHIVE", [
                sys.executable,
                "-u",
                str(resolve(paths.get("database_archiver", "modules/database/swing_history_db.py"))),
                "--run-id",
                ctx.run_id,
                "--db",
                str(database_path),
                "--run-manifest",
                str(run_manifest_path),
                "--yahoo-manifest",
                str(manifest_dir / f"YAHOO_REFRESH_MANIFEST_{ctx.run_id}.json"),
                "--broker-manifest",
                str(fusion_manifest_path),
                "--historical-dir",
                str(hist),
                "--technical",
                str(resolve(paths.get("technical_output_dir", "data/output/technical")) / "latest_technical_features.csv"),
                "--candidates",
                str(candidate),
                "--broker-summary",
                str(broker_summary),
                "--fusion",
                str(decision_source),
                "--decision",
                str(final),
                "--exit-dir",
                str(exit_dir),
                "--multiday-dir",
                str(resolve(paths.get("broker_multiday_output_dir", "data/output/broker_multiday"))),
                "--data-quality-status",
                str(manifest.get("Data_Quality_Status", "VALID")),
                "--summary-output",
                str(database_summary),
            ])
        except Exception as exc:
            manifest["Pipeline_Status"] = "FAILED"
            manifest["Finished_At"] = now_wib().isoformat(timespec="seconds")
            manifest.setdefault("Errors", []).append(f"DATABASE_ARCHIVE_FAILED:{exc}")
            write_json(run_manifest_path, manifest)
            raise
        manifest["Output_Files"]["Database"] = str(database_path)
        manifest["Output_Files"]["Database_Summary"] = str(database_summary)
    manifest["Finished_At"] = now_wib().isoformat(timespec="seconds")
    manifest["Pipeline_Status"] = "SUCCESS"
    write_json(run_manifest_path, manifest)
    return manifest
