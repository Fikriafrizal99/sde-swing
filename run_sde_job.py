#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Callable

from modules.global_market.global_market_snapshot import build_global_market_snapshot
from modules.market_data.market_outlook_regime import calculate_market_outlook_regime, save_market_outlook_regime
from modules.market_data.sector_rotation import produce_sector_rotation
from modules.job_runner.core import (
    broker_readiness,
    load_technical_snapshot,
    run_interactive_broker_break,
    run_command,
    run_broker_fusion_from_snapshot,
    run_broker_multiday_stage,
    run_post_market_technical_stage,
    run_zapi_enrichment,
    _universe_symbols,
    SourceValidationBlocked,
    run_final_from_snapshot,
    run_master_pipeline,
    sync_outcome_tracker,
    try_import_existing_broker_export,
    wait_for_broker_ready,
)
from modules.job_runner.delivery import deliver, telegram_configured
from modules.job_runner.reports import (
    final_watchlist_payloads,
    full_manual_payloads,
    market_outlook_payload,
    broker_waiting_payload,
    preliminary_watchlist_payloads,
    post_market_payloads,
    write_payloads,
)
from modules.runtime_config import write_runtime_config_audit
from modules.job_runner.runtime import (
    EXIT_DELIVERY_FAILED,
    EXIT_DUPLICATE,
    EXIT_FAILED,
    EXIT_RESOURCE_LOCKED,
    EXIT_SKIPPED,
    EXIT_SUCCESS,
    EXIT_WAITING_DATA,
    FileLock,
    JobAlreadyRunning,
    ResourceLocked,
    append_job_log,
    load_context,
    resolve,
    trading_day_status,
    write_status,
    write_json,
    read_json,
)
from modules.job_runner.enhanced_runtime_bridge import (
    broker_multiday_payloads as enhanced_broker_multiday_payloads,
    broker_summary_payloads as enhanced_broker_summary_payloads,
    final_watchlist_payloads as enhanced_final_watchlist_payloads,
    lifecycle_payloads as enhanced_lifecycle_payloads,
    market_outlook_payloads as enhanced_market_outlook_payloads,
    post_market_payloads as enhanced_post_market_payloads,
)
from modules.job_runner.report_validation import ReportSourceValidationError, record_validation_error
from modules.decision.adapter import canonicalize_candidates
from modules.runtime.jobs import INTEGRATED_JOB_NAMES, JOB_DEPENDENCIES, validate_dependency_status


def _finish(ctx, status: str, stage: str, code: int, details: dict | None = None) -> int:
    if bool(getattr(ctx, "_suppress_terminal_status", False)) or bool(
        getattr(ctx, "_full_manual_child_stage", False)
    ):
        result = {
            "status": status, "stage": stage, "exit_code": code, "details": details or {},
        }
        # Reconciliation rows can contain hundreds of symbols.  Keep the
        # durable result complete, but make the operational log bounded so a
        # terminal event is never delayed or buried by a multi-megabyte row
        # dump.
        log_result = dict(result)
        log_details = dict(result["details"])
        reconciliation = log_details.get("source_reconciliation")
        if isinstance(reconciliation, dict):
            compact = {key: value for key, value in reconciliation.items() if key != "rows"}
            compact["row_count"] = len(reconciliation.get("rows", []))
            log_details["source_reconciliation"] = compact
        log_result["details"] = log_details
        append_job_log(ctx, "ENGINE_STAGE_RESULT", str(log_result))
        if bool(getattr(ctx, "_suppress_terminal_status", False)) and not bool(
            getattr(ctx, "_full_manual_child_stage", False)
        ):
            write_json(ctx.status_root / f"engine_result_{ctx.run_id}.json", result)
        return code
    write_status(ctx, status, stage, code, details)
    return code


def _official_runtime(ctx) -> bool:
    """True for the versioned 1.7 multi-source runtime, false for test/legacy contexts."""
    return str(ctx.config_provenance.get("config_version", "")) == "1.7.0-multisource"


def _reports_enabled(ctx) -> bool:
    return not bool(getattr(ctx, "_suppress_reports", False))


def _status_after_delivery(delivery: list[dict]) -> str:
    if any(item.get("status") == "FAILED" for item in delivery):
        return "DELIVERY_FAILED"
    if delivery and all(item.get("status") == "DUPLICATE_SUPPRESSED" for item in delivery):
        # Idempotency suppression means the report was already delivered; it
        # does not mean the engine stage was skipped.  Keep the run terminal
        # and dependency-safe while retaining the delivery detail in status.
        return "SUCCESS_WITH_WARNING"
    if any(item.get("status") == "SKIPPED_NOT_CONFIGURED" for item in delivery):
        return "SUCCESS_WITH_WARNING"
    return "SUCCESS"


def _exit_after_delivery(ctx, delivery: list[dict]) -> int:
    if any(item.get("status") == "FAILED" for item in delivery):
        return EXIT_DELIVERY_FAILED
    if delivery and all(item.get("status") == "DUPLICATE_SUPPRESSED" for item in delivery):
        return EXIT_SUCCESS
    return EXIT_SUCCESS


def _delivery_summary(delivery: list[dict]) -> dict:
    ids: list = []
    for item in delivery:
        if item.get("telegram_message_ids"):
            ids.extend(item.get("telegram_message_ids", []))
        elif item.get("telegram_message_id"):
            ids.append(item.get("telegram_message_id"))
    return {
        "telegram_status": "FAILED" if any(item.get("status") == "FAILED" for item in delivery) else ("SKIPPED_NOT_CONFIGURED" if any(item.get("status") == "SKIPPED_NOT_CONFIGURED" for item in delivery) else ("SKIPPED" if all(item.get("status") in {"DRY_RUN", "NO_TELEGRAM", "DUPLICATE_SUPPRESSED"} for item in delivery) else "SENT")),
        "telegram_message_ids": ids,
        "telegram_part_count": sum(int(item.get("part_count") or 0) for item in delivery if item.get("status") in {"SENT", "DRY_RUN", "NO_TELEGRAM", "DUPLICATE_SUPPRESSED"}),
    }


def _manifest_from_existing_snapshot(ctx, snapshot: dict, warning: str = "") -> dict:
    warnings = [warning] if warning else []
    return {
        "Run_ID": ctx.run_id,
        "Pipeline_Status": "SUCCESS_WITH_EXISTING_SNAPSHOT" if warning else "SUCCESS",
        "Stage": "POST_MARKET_EXISTING_SNAPSHOT",
        "Data_Source": snapshot.get("source_provider", "EXISTING_SNAPSHOT"),
        "Data_Quality_Status": "VALID_WITH_REFRESH_FALLBACK" if warning else snapshot.get("data_quality_status", "VALID"),
        "Technical_Date": snapshot.get("trade_date", ctx.trade_date.isoformat()),
        "Snapshot_ID": snapshot.get("snapshot_id", ""),
        "Snapshot_Manifest": snapshot.get("manifest_path", ""),
        "Candidate_Count": snapshot.get("candidate_count", 0),
        "provider_status": snapshot.get("source_metadata", {}).get("provider_status", ""),
        "data_source_mode": snapshot.get("source_metadata", {}).get("data_source_mode", ""),
        "source_coverage_ratio": snapshot.get("source_metadata", {}).get("source_coverage_ratio"),
        "source_metadata": snapshot.get("source_metadata", {}),
        "Reconciliation_Status": snapshot.get("reconciliation", {}).get("status", ""),
        "Reconciliation_Manifest": snapshot.get("reconciliation", {}).get("json_path", ""),
        "reconciliation": snapshot.get("reconciliation", {}),
        "Warnings": warnings,
        "Output_Files": snapshot.get("output_paths", {}),
    }


def _market_source_details(ctx, snapshot: dict) -> dict:
    manager = _source_details(ctx, record_type="MarketIndex")
    # Global Market has its own historical Yahoo provider; preserve that
    # provider's truthful mode while retaining manager readiness/health.
    manager.update({
        "primary_provider": snapshot.get("provider") or manager.get("primary_provider", ""),
        "provider_status": snapshot.get("provider") or manager.get("provider_status", ""),
        "data_source_mode": snapshot.get("source_mode") or manager.get("data_source_mode", ""),
        "source_coverage_ratio": snapshot.get("coverage_ratio", manager.get("source_coverage_ratio", 0.0)),
    })
    return manager


def job_market_outlook(ctx) -> int:
    print("[1/5] Menyiapkan snapshot global market...", flush=True)
    global_snapshot = getattr(ctx, "_prepared_global_snapshot", None)
    if not isinstance(global_snapshot, dict) or not global_snapshot:
        global_snapshot = build_global_market_snapshot(
            ctx,
            # Existing snapshots are a compatibility preview only.  The versioned
            # runtime must fail closed when the current refresh is not valid.
            fallback_to_existing_on_failure=not _official_runtime(ctx),
        )
    coverage = float(global_snapshot.get("coverage_ratio", 0.0) or 0.0)
    minimum_coverage = float(global_snapshot.get("minimum_required_coverage_ratio", 0.5) or 0.5)
    print(
        f"[2/5] Snapshot global siap: coverage {coverage:.0%}, minimum {minimum_coverage:.0%}",
        flush=True,
    )

    ihsg_path = ctx.path("ihsg_csv", "data/input/IHSG.csv")
    technical_path = ctx.path("technical_output_dir", "data/output/technical") / "latest_technical_features.csv"
    ihsg_refresh_warning = ""
    if not ctx.dry_run and ihsg_path.exists():
        print("[3/5] Memperbarui IHSG dan menghitung regime terbaru...", flush=True)
        try:
            run_command(ctx, "MARKET OUTLOOK IHSG UPDATER", [
                sys.executable,
                "-u",
                str(ctx.path("ihsg_updater", "modules/market_data/update_ihsg.py")),
                "--output",
                str(ihsg_path),
                "--period",
                str(ctx.config.get("download", {}).get("period", "2y")),
            ])
        except Exception as exc:
            ihsg_refresh_warning = f"IHSG_REFRESH_FALLBACK: {exc}"
            append_job_log(ctx, "MARKET_OUTLOOK_IHSG_FALLBACK", ihsg_refresh_warning)
            print("[fallback] Refresh IHSG gagal; data IHSG existing dipakai.", flush=True)
    else:
        print("[3/5] Menghitung regime dari data IHSG existing...", flush=True)

    market_status = calculate_market_outlook_regime(
        ihsg_path=ihsg_path,
        technical_path=technical_path,
        as_of_date=ctx.trade_date,
    )
    market_status.update({
        "provider": global_snapshot.get("provider"),
        "source_mode": global_snapshot.get("source_mode"),
        "coverage": global_snapshot.get("coverage_ratio"),
    })
    metadata_path = None
    rotation_metadata = str(ctx.config.get("paths", {}).get("sector_rotation_metadata", "")).strip()
    if rotation_metadata:
        metadata_path = resolve(rotation_metadata)
    zapi_enrichment = run_zapi_enrichment(
        ctx,
        symbols=_universe_symbols(
            ctx.path("normalized_watchlist", "modules/historical_downloader/Stockbit_Watchlist_2026-07-19_normalized.csv"),
            ctx.path("historical_dir", "data/output/historical/by_symbol"),
        ),
        historical_dir=ctx.path("historical_dir", "data/output/historical/by_symbol"),
        metadata_csv_path=metadata_path,
    )
    market_status.update({
        "zapi_status": zapi_enrichment.get("status", "DEGRADED"),
        "zapi_request_count": zapi_enrichment.get("request_count", 0),
        "zapi_request_cap": zapi_enrichment.get("request_cap", 5),
        "metadata_cache_status": zapi_enrichment.get("metadata_cache_status", ""),
        "metadata_cache_date": zapi_enrichment.get("metadata_cache_date", ""),
        "market_activity_cache_status": zapi_enrichment.get("market_activity_cache_status", ""),
        "suspended_count": zapi_enrichment.get("suspended_count", 0),
        "uma_count": zapi_enrichment.get("uma_count", 0),
        "relisting_count": zapi_enrichment.get("relisting_count", 0),
        "zapi_degraded": bool(zapi_enrichment.get("degraded")),
        "zapi_degraded_reason": zapi_enrichment.get("degraded_reason", ""),
    })
    configured_rotation = str(ctx.config.get("paths", {}).get("sector_rotation_output", "")).strip()
    if configured_rotation:
        rotation_path = resolve(configured_rotation)
        market_status["sector_metadata_status"] = zapi_enrichment.get("metadata_cache_status", "UNAVAILABLE")
        market_status["sector_metadata_source_mode"] = "CACHE" if zapi_enrichment.get("metadata_cache_status") in {"HIT", "STALE_FALLBACK"} else "ZAPI"
        market_status["sector_metadata_request_count"] = zapi_enrichment.get("request_count", 0)
        rotation_payload = produce_sector_rotation(
            technical_path,
            rotation_path,
            ctx.trade_date,
            metadata_path=metadata_path,
        )
        market_status["sector_rotation_path"] = str(rotation_path)
        market_status["sector_rotation_status"] = rotation_payload.get("status", "INSUFFICIENT_DATA")
        market_status["sector_rotation_coverage"] = rotation_payload.get("coverage", 0.0)
    if ihsg_refresh_warning:
        market_status.setdefault("warnings", []).append(ihsg_refresh_warning)
    regime_output = save_market_outlook_regime(
        market_status,
        ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json",
    )
    print(
        f"      Regime {market_status.get('market_regime', 'UNKNOWN')} | "
        f"confidence {float(market_status.get('confidence_pct', 0.0) or 0.0):.0f}% | "
        f"data {market_status.get('data_date') or 'tidak tersedia'}",
        flush=True,
    )

    payloads = []
    if _reports_enabled(ctx):
        payloads = (
            enhanced_market_outlook_payloads(ctx, global_snapshot, market_status)
            if _official_runtime(ctx)
            else market_outlook_payload(ctx, global_snapshot, market_status=market_status)
        )
    preview_paths = write_payloads(ctx, payloads) if _reports_enabled(ctx) else []
    print(f"[4/5] Preview dibuat: {len(preview_paths)} file", flush=True)
    if coverage < minimum_coverage:
        print("[5/5] Pengiriman dibatalkan: coverage global market tidak memenuhi guardrail.", flush=True)
        sentiment = global_snapshot.get("global_sentiment", {}) if global_snapshot else {}
        return _finish(ctx, "INVALID_GLOBAL_MARKET_DATA", "MARKET_OUTLOOK_GUARDRAIL", EXIT_FAILED, {
            "snapshot_id": global_snapshot.get("snapshot_id", ""),
            "global_market_snapshot_id": global_snapshot.get("snapshot_id", ""),
            "global_market_coverage_ratio": coverage,
            "global_sentiment_state": sentiment.get("sentiment_state", "INSUFFICIENT_DATA"),
            "global_sentiment_score": sentiment.get("sentiment_score", 0.0),
            "market_regime": market_status.get("market_regime", "UNKNOWN"),
            "market_regime_confidence_pct": market_status.get("confidence_pct", 0.0),
            "market_regime_data_date": market_status.get("data_date"),
            "data_source_mode": global_snapshot.get("source_mode", ""),
            "provider_status": global_snapshot.get("provider", ""),
            **_market_source_details(ctx, global_snapshot),
            "warnings": [*global_snapshot.get("warnings", []), *market_status.get("warnings", [])],
            "errors": global_snapshot.get("errors", []),
            "output_paths": {
                "global_market_snapshot": str(resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"),
                "market_outlook_regime": str(regime_output),
                "sector_rotation": str(resolve(configured_rotation)) if configured_rotation else "",
            },
            "preview_paths": [str(p) for p in preview_paths],
            "telegram_status": "NOT_SENT_INVALID_DATA",
            "minimum_required_coverage_ratio": minimum_coverage,
            "sector_rotation_status": market_status.get("sector_rotation_status", ""),
            "sector_rotation_coverage": market_status.get("sector_rotation_coverage", 0.0),
            "zapi_request_count": market_status.get("zapi_request_count", 0),
            "zapi_request_cap": market_status.get("zapi_request_cap", 5),
            "metadata_cache_status": market_status.get("metadata_cache_status", ""),
            "metadata_cache_date": market_status.get("metadata_cache_date", ""),
            "market_activity_cache_status": market_status.get("market_activity_cache_status", ""),
            "suspended_count": market_status.get("suspended_count", 0),
            "uma_count": market_status.get("uma_count", 0),
            "relisting_count": market_status.get("relisting_count", 0),
            "zapi_degraded": market_status.get("zapi_degraded", False),
            "zapi_degraded_reason": market_status.get("zapi_degraded_reason", ""),
        })
    print("[5/5] Mengirim Market Outlook ke Telegram...", flush=True)
    delivery = deliver(ctx, payloads) if _reports_enabled(ctx) else []
    sentiment = global_snapshot.get("global_sentiment", {}) if global_snapshot else {}
    code = _exit_after_delivery(ctx, delivery)
    market_status_after_delivery = _status_after_delivery(delivery)
    sector_status = str(market_status.get("sector_rotation_status", "VALID")).upper()
    if sector_status != "VALID" and market_status_after_delivery == "SUCCESS":
        market_status_after_delivery = "SUCCESS_WITH_WARNING"
    return _finish(ctx, market_status_after_delivery, "MARKET_OUTLOOK", code, {
        "snapshot_id": global_snapshot.get("snapshot_id", ""),
        "global_market_snapshot_id": global_snapshot.get("snapshot_id", ""),
        "global_market_coverage_ratio": global_snapshot.get("coverage_ratio", 0.0),
        "global_sentiment_state": sentiment.get("sentiment_state", "INSUFFICIENT_DATA"),
        "global_sentiment_score": sentiment.get("sentiment_score", 0.0),
        "market_regime": market_status.get("market_regime", "UNKNOWN"),
        "market_regime_score": market_status.get("regime_score", 0.0),
        "market_regime_confidence_pct": market_status.get("confidence_pct", 0.0),
        "market_regime_data_date": market_status.get("data_date"),
        "data_source_mode": global_snapshot.get("source_mode", ""),
        "provider_status": global_snapshot.get("provider", ""),
        **_market_source_details(ctx, global_snapshot),
        "warnings": [
            *global_snapshot.get("warnings", []),
            *market_status.get("warnings", []),
            *([] if sector_status == "VALID" else [f"SECTOR_ROTATION_{sector_status}"]),
        ],
        "sector_rotation_status": sector_status,
        "sector_rotation_coverage": market_status.get("sector_rotation_coverage", 0.0),
        "zapi_request_count": market_status.get("zapi_request_count", 0),
        "zapi_request_cap": market_status.get("zapi_request_cap", 5),
        "metadata_cache_status": market_status.get("metadata_cache_status", ""),
        "metadata_cache_date": market_status.get("metadata_cache_date", ""),
        "market_activity_cache_status": market_status.get("market_activity_cache_status", ""),
        "suspended_count": market_status.get("suspended_count", 0),
        "uma_count": market_status.get("uma_count", 0),
        "relisting_count": market_status.get("relisting_count", 0),
        "zapi_degraded": market_status.get("zapi_degraded", False),
        "zapi_degraded_reason": market_status.get("zapi_degraded_reason", ""),
        "errors": global_snapshot.get("errors", []),
        "output_paths": {
            "global_market_snapshot": str(resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"),
            "market_outlook_regime": str(regime_output),
            "sector_rotation": str(resolve(configured_rotation)) if configured_rotation else "",
        },
        "preview_paths": [str(p) for p in preview_paths],
        "delivery": delivery,
        **_delivery_summary(delivery),
    })


def job_post_market(ctx) -> int:
    manifest = {}
    if ctx.preview_existing:
        append_job_log(ctx, "POST_MARKET_PREVIEW_EXISTING", "Existing snapshot reused for preview generation")
        print("[1/4] Memuat snapshot teknikal existing...", flush=True)
        snapshot = load_technical_snapshot(ctx)
        if snapshot.get("status") != "VALID":
            raise RuntimeError(snapshot.get("reason") or snapshot.get("status") or "TECHNICAL_SNAPSHOT_NOT_FOUND")
        manifest = _manifest_from_existing_snapshot(ctx, snapshot)
    else:
        print("[1/4] Refresh Yahoo saham dan membangun snapshot teknikal...", flush=True)
        try:
            manifest = run_post_market_technical_stage(ctx)
        except SourceValidationBlocked as exc:
            return _finish(ctx, "WAITING_DATA", "ZAPI_SOURCE_VALIDATION", EXIT_WAITING_DATA, {
                "error": str(exc),
                "errors": [str(exc)],
                "source_reconciliation": exc.result,
                "provider_status": exc.result.get("status", "ZAPI_MISSING_CREDENTIAL"),
                "data_source_mode": exc.result.get("source_mode", "NOT_CONFIGURED"),
                "symbols_requested": exc.result.get("symbols_requested", 0),
                "source_coverage_ratio": exc.result.get("coverage_ratio", 0.0),
            })
        except Exception as exc:
            allow_fallback = (
                not _official_runtime(ctx)
                and bool(ctx.scheduler_config.get("post_market", {}).get("fallback_to_existing_on_refresh_failure", True))
            )
            snapshot = load_technical_snapshot(ctx) if allow_fallback else {}
            if allow_fallback and snapshot.get("status") == "VALID":
                warning = f"REFRESH_FAILED_USING_EXISTING_SNAPSHOT: {exc}"
                append_job_log(ctx, "POST_MARKET_REFRESH_FALLBACK", warning)
                print("[fallback] Refresh gagal; snapshot teknikal existing hari ini dipakai.", flush=True)
                manifest = _manifest_from_existing_snapshot(ctx, snapshot, warning=warning)
            else:
                trace_dir = resolve("data/output/job_status/tracebacks")
                trace_dir.mkdir(parents=True, exist_ok=True)
                trace_path = trace_dir / f"{ctx.run_id}-post-market.txt"
                rendered = traceback.format_exc()
                trace_path.write_text(rendered, encoding="utf-8")
                append_job_log(ctx, "POST_MARKET_STAGE_EXCEPTION", rendered)
                return _finish(ctx, "FAILED", "POST_MARKET_EXCEPTION", EXIT_FAILED, {
                    "error": str(exc), "errors": [str(exc)], "traceback_path": str(trace_path),
                })
    stage_manifest_path = ctx.path("manifest_dir", "data/output/manifests") / f"SWING_RUN_MANIFEST_{ctx.run_id}.json"
    manifest["Run_ID"] = ctx.run_id
    manifest["Manifest_Path"] = str(stage_manifest_path)
    write_json(stage_manifest_path, manifest)
    navigator_path = str(manifest.get("Broker_Navigator_Path") or "").strip()
    if navigator_path:
        print(f"[BROKER] BROKER_NAVIGATOR_SYMBOLS siap: {navigator_path}", flush=True)
    if "symbols_loaded" in manifest and int(manifest.get("symbols_loaded", 0) or 0) <= 0:
        warning = "POST_MARKET_EMPTY_SNAPSHOT: tidak ada simbol valid yang dimuat"
        append_job_log(ctx, "POST_MARKET_EMPTY_SNAPSHOT", warning)
        return _finish(ctx, "PARTIAL", "POST_MARKET_EMPTY_SNAPSHOT", EXIT_WAITING_DATA, {
            "data_status": "EMPTY_SNAPSHOT",
            "symbols_requested": manifest.get("symbols_requested", 0),
            "symbols_loaded": manifest.get("symbols_loaded", 0),
            "symbols_valid": manifest.get("symbols_valid", 0),
            "symbols_failed": manifest.get("symbols_failed", 0),
            "symbols_skipped": manifest.get("symbols_skipped", 0),
            "provider_status": manifest.get("provider_status") or manifest.get("source_metadata", {}).get("provider_status", "" if _official_runtime(ctx) else "NOT_CONFIGURED"),
            "data_source_mode": manifest.get("data_source_mode") or manifest.get("source_metadata", {}).get("data_source_mode", "" if _official_runtime(ctx) else "NOT_CONFIGURED"),
            "source_coverage_ratio": manifest.get("source_coverage_ratio", 0.0),
            "snapshot_ids": manifest.get("snapshot_ids", {"technical": manifest.get("Snapshot_ID", "")}),
            "snapshot_id": manifest.get("Snapshot_ID", ""),
            "warnings": [warning],
        })
    if not ctx.dry_run:
        try:
            print("[analytics] Memperbarui outcome rekomendasi lama...", flush=True)
            sync_outcome_tracker(ctx)
            manifest["Outcome_Tracker_Status"] = "SUCCESS"
        except Exception as exc:
            warning = f"OUTCOME_TRACKER_WARNING: {exc}"
            append_job_log(ctx, "OUTCOME_TRACKER_WARNING", warning)
            manifest.setdefault("Warnings", []).append(warning)
            print(f"[analytics warning] {exc}", flush=True)
    print("[2/4] Membentuk satu ringkasan Post Market...", flush=True)
    payloads = []
    if _reports_enabled(ctx):
        payloads = (
            enhanced_post_market_payloads(ctx, manifest)
            if _official_runtime(ctx)
            else post_market_payloads(ctx, manifest)
        )
    preview_paths = write_payloads(ctx, payloads) if _reports_enabled(ctx) else []
    print(f"[3/4] Preview dibuat: {len(preview_paths)} file", flush=True)
    print("[4/4] Mengirim ringkasan Post Market ke Telegram...", flush=True)
    delivery = deliver(ctx, payloads) if _reports_enabled(ctx) else []
    code = _exit_after_delivery(ctx, delivery)
    return _finish(ctx, _status_after_delivery(delivery), "POST_MARKET", code, {
        "snapshot_id": manifest.get("Snapshot_ID", ""),
        "snapshot_trade_date": manifest.get("Technical_Date", ""),
        "data_status": manifest.get("Data_Quality_Status", ""),
        "symbols_requested": manifest.get("symbols_requested", manifest.get("symbols_loaded", 0)),
        "symbols_loaded": manifest.get("symbols_loaded", 0),
        "symbols_valid": manifest.get("symbols_valid", 0),
        "symbols_failed": manifest.get("symbols_failed", 0),
        "symbols_skipped": manifest.get("symbols_skipped", 0),
        "provider_status": manifest.get("provider_status") or manifest.get("source_metadata", {}).get("provider_status", "" if _official_runtime(ctx) else "NOT_CONFIGURED"),
        "data_source_mode": manifest.get("data_source_mode") or manifest.get("source_metadata", {}).get("data_source_mode", "" if _official_runtime(ctx) else "NOT_CONFIGURED"),
        "source_coverage_ratio": manifest.get("source_coverage_ratio", 0.0),
        "zapi_request_count": manifest.get("Zapi_Request_Count", manifest.get("source_metadata", {}).get("zapi_request_count", 0)),
        "zapi_request_cap": manifest.get("Zapi_Request_Cap", manifest.get("source_metadata", {}).get("zapi_request_cap", 5)),
        "metadata_cache_status": manifest.get("Zapi_Metadata_Cache_Status", manifest.get("source_metadata", {}).get("metadata_cache_status", "")),
        "metadata_cache_date": manifest.get("Zapi_Metadata_Cache_Date", manifest.get("source_metadata", {}).get("metadata_cache_date", "")),
        "market_activity_cache_status": manifest.get("Zapi_Market_Activity_Cache_Status", manifest.get("source_metadata", {}).get("market_activity_cache_status", "")),
        "suspended_count": manifest.get("Suspended_Symbol_Count", manifest.get("source_metadata", {}).get("suspended_count", 0)),
        "uma_count": manifest.get("Uma_Symbol_Count", manifest.get("source_metadata", {}).get("uma_count", 0)),
        "relisting_count": manifest.get("Relisting_Symbol_Count", manifest.get("source_metadata", {}).get("relisting_count", 0)),
        "zapi_degraded": manifest.get("Zapi_Degraded", manifest.get("source_metadata", {}).get("zapi_degraded", False)),
        "snapshot_ids": manifest.get("snapshot_ids", {"technical": manifest.get("Snapshot_ID", "")}),
        "warnings": manifest.get("Warnings", []),
        "preview_paths": [str(p) for p in preview_paths],
        "delivery": delivery,
        **_delivery_summary(delivery),
        "source_run_id": manifest.get("Run_ID", ctx.run_id) if manifest else "EXISTING_SNAPSHOT",
    })


def job_final_watchlist(ctx) -> int:
    print("[1/6] Memeriksa snapshot teknikal dan Broker Summary...", flush=True)
    preview_dependency = _validate_preview_existing_dependencies(ctx) if ctx.preview_existing else None
    dependency = _require_integrated_dependencies(ctx, "final_watchlist")
    if dependency and _interactive_broker_dependency_recovery_allowed(ctx, dependency):
        append_job_log(
            ctx,
            "INTERACTIVE_BROKER_DEPENDENCY_RECOVERY",
            json.dumps({"blocked_dependencies": ["broker_summary", "broker_multi_day"]}),
        )
        print("[2/6] Dependency broker belum siap; melanjutkan ke broker break interaktif...", flush=True)
        dependency = None
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {
            "dependency_status": dependency,
            "errors": ["FINAL_WATCHLIST_DEPENDENCY_NOT_READY"],
            "warnings": ["Dependencies must match trade_date, run status, and config_version."],
        })
    if preview_dependency is not None:
        ready = True
        detail = dict(preview_dependency.get("broker_readiness", {}))
        detail["preview_existing"] = True
        print("[2/6] Artefak kanonik existing valid; status delivery lama diabaikan.", flush=True)
    elif ctx.interactive_broker:
        ready, detail = broker_readiness(ctx)
        if not ready and detail.get("status") not in {"STALE_TECHNICAL_SNAPSHOT", "INVALID_DEPENDENCY"}:
            print("[BROKER] Broker Summary hari ini belum siap. Memulai broker break manual...", flush=True)
            ready, detail = run_interactive_broker_break(ctx, detail)
    else:
        ready, detail = wait_for_broker_ready(ctx)
    if not ready:
        policy = str(ctx.scheduler_config.get("final_watchlist", {}).get("missing_broker_policy", "skip_final_watchlist"))
        if policy == "send_preliminary_watchlist" and detail.get("status") not in {"STALE_TECHNICAL_SNAPSHOT", "INVALID_DEPENDENCY"}:
            payloads = (
                broker_waiting_payload(ctx, detail) + preliminary_watchlist_payloads(ctx, detail)
                if _reports_enabled(ctx)
                else []
            )
            preview_paths = write_payloads(ctx, payloads) if _reports_enabled(ctx) else []
            delivery = deliver(ctx, payloads) if _reports_enabled(ctx) else []
            return _finish(ctx, "PARTIAL", "PRELIMINARY_WATCHLIST", EXIT_SUCCESS, {
                **detail,
                "broker_readiness_status": detail.get("status", detail.get("reason", "")),
                "preview_paths": [str(p) for p in preview_paths],
                "delivery": delivery,
                **_delivery_summary(delivery),
                "warnings": ["PRELIMINARY_ONLY; broker summary belum valid"],
            })
        payloads = broker_waiting_payload(ctx, detail) if _reports_enabled(ctx) else []
        preview_paths = write_payloads(ctx, payloads) if _reports_enabled(ctx) else []
        delivery = deliver(ctx, payloads) if _reports_enabled(ctx) else []
        status = "WAITING_DATA_TIMEOUT" if detail.get("status") == "WAITING_DATA_TIMEOUT" or detail.get("reason") == "BROKER_CUTOFF_REACHED" else "WAITING_DATA"
        if detail.get("status") in {"STALE_TECHNICAL_SNAPSHOT", "INVALID_DEPENDENCY"}:
            status = "INVALID_DATA"
        return _finish(ctx, status, "BROKER_READINESS", EXIT_WAITING_DATA, {
            **detail,
            "broker_readiness_status": detail.get("status", detail.get("reason", "")),
            "preview_paths": [str(p) for p in preview_paths],
            "delivery": delivery,
            **_delivery_summary(delivery),
        })
    print(f"[2/6] Broker Summary valid: {detail.get('matched_symbols', 0)}/{detail.get('expected_symbols', 0)} simbol", flush=True)
    print("[3/6] Menjalankan Broker Fusion, Decision Engine, dan Entry Plan...", flush=True)
    manifest = run_final_from_snapshot(ctx)
    if not ctx.dry_run:
        try:
            print("[analytics] Mencatat sinyal dan memperbarui outcome...", flush=True)
            decision_path = resolve("data/output/decision/FINAL_DECISION_V3.csv")
            entry_plans_path = resolve("data/output/exit/ENTRY_PLANS.csv")
            sync_outcome_tracker(ctx, decision_path, entry_plans_path, ctx.trade_date.isoformat())
            manifest["Outcome_Tracker_Status"] = "SUCCESS"
            manifest.setdefault("Output_Files", {})["Analytics"] = str(
                resolve(ctx.config.get("paths", {}).get("analytics_output_root", "data/output/analytics")) / "performance"
            )
        except Exception as exc:
            warning = f"OUTCOME_TRACKER_WARNING: {exc}"
            append_job_log(ctx, "OUTCOME_TRACKER_WARNING", warning)
            manifest.setdefault("Warnings", []).append(warning)
            print(f"[analytics warning] {exc}", flush=True)
    print("[4/6] Membentuk Final Watchlist dan detail broker kandidat...", flush=True)
    payloads = []
    if _reports_enabled(ctx):
        payloads = (
            enhanced_final_watchlist_payloads(ctx, manifest)
            + enhanced_lifecycle_payloads(ctx)
            if _official_runtime(ctx)
            else final_watchlist_payloads(ctx, manifest)
        )
    preview_paths = write_payloads(ctx, payloads) if _reports_enabled(ctx) else []
    print(f"[5/6] Preview dibuat: {len(preview_paths)} file", flush=True)
    print("[6/6] Mengirim Final Watchlist ke Telegram...", flush=True)
    delivery = deliver(ctx, payloads) if _reports_enabled(ctx) else []
    code = _exit_after_delivery(ctx, delivery)
    return _finish(ctx, _status_after_delivery(delivery), "FINAL_WATCHLIST", code, {
        "broker_readiness": detail,
        "broker_readiness_status": detail.get("status", detail.get("reason", "")),
        "broker_date": detail.get("broker_date", ""),
        "snapshot_id": detail.get("snapshot_id", manifest.get("Technical_Snapshot_ID", "")),
        "snapshot_trade_date": detail.get("snapshot_trade_date", manifest.get("Technical_Date", "")),
        "preview_paths": [str(p) for p in preview_paths],
        "delivery": delivery,
        **_delivery_summary(delivery),
    })


def job_full_manual(ctx) -> int:
    if _official_runtime(ctx):
        # Full Manual is the same stage graph as the scheduled jobs.  Reports
        # are emitted once, after all engine artifacts have passed validation.
        original_job = ctx.job
        original_no_telegram = ctx.no_telegram
        original_suppress_reports = bool(getattr(ctx, "_suppress_reports", False))
        setattr(ctx, "_suppress_reports", True)
        ctx.no_telegram = True
        stage_jobs = (
            ("global_market_preparation", None),
            ("post_market", job_post_market),
            ("technical_snapshot", job_technical_snapshot),
            ("universe_selection", job_universe_selection),
            ("candidate_selection", job_candidate_selection),
            ("market_outlook", job_market_outlook),
            ("broker_summary", job_broker_summary),
            ("broker_multi_day", job_broker_multi_day),
            ("final_watchlist", job_final_watchlist),
        )
        stage_results: list[dict] = []
        try:
            for stage_name, handler in stage_jobs:
                if stage_name == "global_market_preparation":
                    setattr(ctx, "_prepared_global_snapshot", build_global_market_snapshot(
                        ctx,
                        fallback_to_existing_on_failure=False,
                    ))
                    stage_results.append({"job": "global_market_preparation", "exit_code": EXIT_SUCCESS})
                    continue
                if handler is None:
                    raise RuntimeError(f"FULL_MANUAL_HANDLER_MISSING:{stage_name}")
                ctx.job = stage_name
                setattr(ctx, "_full_manual_child_stage", True)
                code = handler(ctx)
                setattr(ctx, "_full_manual_child_stage", False)
                stage_results.append({"job": stage_name, "exit_code": code})
                if code not in {EXIT_SUCCESS, EXIT_DUPLICATE}:
                    ctx.job = original_job
                    terminal = "WAITING_DATA" if code == EXIT_WAITING_DATA else "SKIPPED" if code == EXIT_SKIPPED else "FAILED"
                    return _finish(ctx, terminal, "FULL_MANUAL_STAGE", code, {
                        "failed_stage": stage_name,
                        "stage_results": stage_results,
                    })
        finally:
            ctx.job = original_job
            ctx.no_telegram = original_no_telegram
            setattr(ctx, "_suppress_reports", original_suppress_reports)
            setattr(ctx, "_full_manual_child_stage", False)
            if hasattr(ctx, "_prepared_global_snapshot"):
                delattr(ctx, "_prepared_global_snapshot")

        global_snapshot_path = resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"
        global_snapshot = read_json(global_snapshot_path)
        market_status = read_json(
            ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
        )
        run_manifest = read_json(
            ctx.path("manifest_dir", "data/output/manifests") / f"SWING_RUN_MANIFEST_{ctx.run_id}.json"
        )
        payloads = []
        if _reports_enabled(ctx):
            payloads = (
                enhanced_market_outlook_payloads(ctx, global_snapshot, market_status)
                + enhanced_post_market_payloads(ctx, run_manifest)
                + enhanced_broker_summary_payloads(ctx)
                + enhanced_broker_multiday_payloads(ctx)
                + enhanced_final_watchlist_payloads(ctx, run_manifest)
                + enhanced_lifecycle_payloads(ctx)
            )
        preview_paths = write_payloads(ctx, payloads) if _reports_enabled(ctx) else []
        delivery = deliver(ctx, payloads) if _reports_enabled(ctx) else []
        code = _exit_after_delivery(ctx, delivery)
        return _finish(ctx, _status_after_delivery(delivery), "FULL_MANUAL", code, {
            "stage_results": stage_results,
            "preview_paths": [str(p) for p in preview_paths],
            "delivery": delivery,
            **_delivery_summary(delivery),
            "source_run_id": run_manifest.get("Run_ID", ctx.run_id),
        })

    manifest = run_master_pipeline(
        ctx,
        refresh_data=True,
        interactive_yahoo=True,
        scheduler=False,
        skip_broker_wait=False,
        broker_date_policy=None,
    )
    if not ctx.dry_run:
        try:
            sync_outcome_tracker(
                ctx,
                resolve("data/output/decision/FINAL_DECISION_V3.csv"),
                resolve("data/output/exit/ENTRY_PLANS.csv"),
                str(manifest.get("Technical_Date", ctx.trade_date.isoformat())),
            )
            manifest["Outcome_Tracker_Status"] = "SUCCESS"
        except Exception as exc:
            warning = f"OUTCOME_TRACKER_WARNING: {exc}"
            append_job_log(ctx, "OUTCOME_TRACKER_WARNING", warning)
            manifest.setdefault("Warnings", []).append(warning)
            print(f"[analytics warning] {exc}", flush=True)
    payloads = full_manual_payloads(ctx, manifest)
    preview_paths = write_payloads(ctx, payloads)
    delivery = deliver(ctx, payloads)
    code = _exit_after_delivery(ctx, delivery)
    return _finish(ctx, _status_after_delivery(delivery), "FULL_MANUAL", code, {
        "preview_paths": [str(p) for p in preview_paths],
        "delivery": delivery,
        **_delivery_summary(delivery),
        "source_run_id": manifest.get("Run_ID", ctx.run_id),
    })


def _source_details(ctx, *, record_type: str | None = None, **kwargs) -> dict:
    try:
        return ctx.source_manager.provider_metadata(record_type=record_type, **kwargs)
    except Exception as exc:
        unavailable = "" if _official_runtime(ctx) else "NOT_CONFIGURED"
        return {
            "primary_provider": unavailable,
            "provider_status": unavailable,
            "data_source_mode": unavailable,
            "providers_attempted": [],
            "fallback_used": False,
            "mock_used": False,
            "source_health": {},
            "source_coverage_ratio": 0.0,
            "warnings": [f"SOURCE_MANAGER_UNAVAILABLE:{exc}"],
        }


def _integrated_statuses(ctx) -> dict[str, dict]:
    root = ctx.status_root
    statuses: dict[str, dict] = {}
    for name in INTEGRATED_JOB_NAMES:
        payload = read_json(root / f"{name}_latest.json")
        if payload:
            statuses[name] = payload

    # The dated snapshot is the canonical technical artifact.  A standalone
    # Post Market run can legitimately create it without running the optional
    # ``technical_snapshot`` status job, so do not reject a current artifact
    # merely because ``technical_snapshot_latest.json`` is from yesterday.
    snapshot = load_technical_snapshot(ctx)
    if snapshot.get("status") == "VALID":
        snapshot_date = str(snapshot.get("trade_date", ctx.trade_date.isoformat()))
        existing = dict(statuses.get("technical_snapshot", {}))
        existing_status = str(existing.get("status", existing.get("status_v1_7", ""))).upper()
        existing_date = str(existing.get("trade_date", ""))
        if existing_status not in {"SUCCESS", "SUCCESS_WITH_WARNING", "PARTIAL"} or existing_date != snapshot_date:
            existing.update({
                "status": "SUCCESS_WITH_WARNING" if "WARNING" in str(snapshot.get("data_quality_status", "")).upper() else "SUCCESS",
                "status_v1_7": "SUCCESS_WITH_WARNING" if "WARNING" in str(snapshot.get("data_quality_status", "")).upper() else "SUCCESS",
                "trade_date": snapshot_date,
                "config_version": str(snapshot.get("config_version", ctx.runtime_version)),
                "snapshot_id": snapshot.get("snapshot_id", ""),
                "snapshot_trade_date": snapshot_date,
                "dependency_status_override": "TECHNICAL_SNAPSHOT_ARTIFACT",
            })
            statuses["technical_snapshot"] = existing
            append_job_log(ctx, "DEPENDENCY_ARTIFACT_RECONCILIATION", json.dumps({
                "dependency": "technical_snapshot",
                "status": existing["status"],
                "trade_date": snapshot_date,
                "snapshot_id": snapshot.get("snapshot_id", ""),
            }))

        # A duplicate-suppressed Telegram delivery must not hide a completed
        # Post Market engine stage.  Use the current snapshot only when the
        # Post Market status itself points to that same artifact.
        post = dict(statuses.get("post_market", {}))
        post_details = post.get("details", {}) if isinstance(post.get("details"), dict) else {}
        post_snapshot_id = str(post.get("snapshot_id") or post_details.get("snapshot_id") or "")
        post_snapshot_date = str(post.get("snapshot_trade_date") or post_details.get("snapshot_trade_date") or "")
        delivery = post_details.get("delivery", []) if isinstance(post_details.get("delivery"), list) else []
        duplicate_only = bool(delivery) and all(
            str(item.get("status", "")).upper() == "DUPLICATE_SUPPRESSED"
            for item in delivery
            if isinstance(item, dict)
        )
        if (
            post
            and str(post.get("status", post.get("status_v1_7", ""))).upper() in {"SKIPPED", "DUPLICATE_SUPPRESSED"}
            and post.get("trade_date") == ctx.trade_date.isoformat()
            and (post_snapshot_id == str(snapshot.get("snapshot_id", "")) or post_snapshot_date == snapshot_date)
            and duplicate_only
        ):
            post["status"] = "SUCCESS_WITH_WARNING"
            post["status_v1_7"] = "SUCCESS_WITH_WARNING"
            post["dependency_status_override"] = "POST_MARKET_ARTIFACT_DELIVERY_DUPLICATE"
            statuses["post_market"] = post
            append_job_log(ctx, "DEPENDENCY_ARTIFACT_RECONCILIATION", json.dumps({
                "dependency": "post_market",
                "status": "SUCCESS_WITH_WARNING",
                "reason": "DUPLICATE_SUPPRESSED_AFTER_ENGINE_SUCCESS",
                "snapshot_id": snapshot.get("snapshot_id", ""),
            }))
    return statuses


def _require_integrated_dependencies(ctx, job_name: str) -> dict | None:
    if ctx.preview_existing and job_name == "final_watchlist":
        check = _validate_preview_existing_dependencies(ctx)
        if check.get("valid"):
            return None
        return check
    # Hand-built legacy test contexts do not carry config provenance. They keep
    # the proven Stage 1/2 behaviour; official load_context runs are strict.
    if str(ctx.config_provenance.get("config_version", "")) != "1.7.0-multisource":
        return None
    check = validate_dependency_status(ctx.runtime_context, job_name, _integrated_statuses(ctx))
    if check.get("valid"):
        return None
    return check


def _interactive_broker_dependency_recovery_allowed(ctx, dependency: dict | None) -> bool:
    """Allow the manual broker break to repair only broker dependencies.

    ``--interactive-broker`` is an input-recovery mode. A stale/failed broker
    summary is expected before today's export arrives, so it must not be
    rejected by the predecessor gate. Market and technical dependencies still
    have to be fresh and config-compatible.
    """

    if not ctx.interactive_broker or not isinstance(dependency, dict):
        return False
    required = {str(item) for item in dependency.get("required", [])}
    if not {"broker_summary", "broker_multi_day"}.issubset(required):
        return False
    dependencies = dependency.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return False
    for name in ("market_outlook", "post_market"):
        item = dependencies.get(name)
        if not isinstance(item, dict):
            return False
        if str(item.get("status", "")).upper() not in {"SUCCESS", "SUCCESS_WITH_WARNING", "PARTIAL"}:
            return False
        if not all(bool(item.get(key)) for key in ("fresh", "date_match", "config_match")):
            return False
    return True


def _validate_preview_existing_dependencies(ctx) -> dict:
    """Validate canonical artifacts for ``--preview-existing``.

    Old status files and historical delivery failures are intentionally ignored
    here.  Date, config hash/version, source manifests, and required output
    files remain mandatory so preview mode cannot turn stale data into a new
    Final Watchlist.
    """
    errors: list[str] = []
    paths = ctx.config.get("paths", {})
    snapshot = load_technical_snapshot(ctx)
    if snapshot.get("status") != "VALID":
        errors.append(str(snapshot.get("reason") or snapshot.get("status") or "TECHNICAL_SNAPSHOT_INVALID"))
    snapshot_outputs = snapshot.get("output_paths", {}) if isinstance(snapshot.get("output_paths"), dict) else {}
    for key in ("technical_features", "technical_candidates"):
        raw = str(snapshot_outputs.get(key, "")).strip()
        if not raw or not Path(raw).exists():
            errors.append(f"TECHNICAL_OUTPUT_MISSING:{key}")

    manifest_dir = ctx.path("manifest_dir", "data/output/manifests")
    candidates: list[tuple[Path, dict]] = []
    for path in manifest_dir.glob("SWING_RUN_MANIFEST_*.json"):
        payload = read_json(path)
        if str(payload.get("Technical_Date", "")) == ctx.trade_date.isoformat():
            candidates.append((path, payload))
    if not candidates:
        errors.append("CANONICAL_RUN_MANIFEST_NOT_FOUND")
        return {"valid": False, "errors": errors, "canonical_artifacts": {}}
    run_manifest_path, run_manifest = max(candidates, key=lambda item: item[0].stat().st_mtime)
    current_config_version = str(ctx.config_provenance.get("config_version", ""))
    current_config_hash = str(ctx.config_provenance.get("config_hash", ""))
    if str(run_manifest.get("Config_Version", "")) != current_config_version:
        errors.append("CANONICAL_CONFIG_VERSION_MISMATCH")
    if current_config_hash and str(run_manifest.get("Config_Hash", "")) != current_config_hash:
        errors.append("CANONICAL_CONFIG_HASH_MISMATCH")
    if str(run_manifest.get("Pipeline_Status", "")).upper() not in {"SUCCESS", "SUCCESS_WITH_WARNING", "SUCCESS_WITH_EXISTING_SNAPSHOT"}:
        errors.append(f"CANONICAL_RUN_STATUS_INVALID:{run_manifest.get('Pipeline_Status', '')}")

    output_files = run_manifest.get("Output_Files", {}) if isinstance(run_manifest.get("Output_Files"), dict) else {}
    required_outputs = {
        "fusion": output_files.get("Fusion") or paths.get("decision_source", "data/input/FINAL_DECISION_V2.csv"),
        "decision": output_files.get("Decision") or resolve(paths.get("decision_output_dir", "data/output/decision")) / "FINAL_DECISION_V3.csv",
        "entry_plans": resolve(paths.get("exit_output_dir", "data/output/exit")) / "ENTRY_PLANS.csv",
    }
    for label, raw in required_outputs.items():
        if not Path(str(raw)).exists():
            errors.append(f"CANONICAL_OUTPUT_MISSING:{label}")

    run_id = str(run_manifest.get("Run_ID", ""))
    fusion_manifest_path = manifest_dir / f"BROKER_FUSION_MANIFEST_{run_id}.json"
    fusion_manifest = read_json(fusion_manifest_path)
    if not fusion_manifest:
        errors.append("BROKER_FUSION_MANIFEST_MISSING")
    else:
        if str(fusion_manifest.get("broker_date", "")) != ctx.trade_date.isoformat():
            errors.append("BROKER_FUSION_DATE_MISMATCH")
        fusion_technical = str(fusion_manifest.get("technical_source", "")).strip()
        candidate_source = str(snapshot_outputs.get("technical_candidates", "")).strip()
        if fusion_technical and candidate_source and Path(fusion_technical).resolve() != Path(candidate_source).resolve():
            errors.append("BROKER_FUSION_TECHNICAL_SOURCE_MISMATCH")
        try:
            coverage = float(fusion_manifest.get("broker_coverage", 0.0) or 0.0)
        except (TypeError, ValueError):
            coverage = 0.0
        if coverage < float(ctx.config.get("broker", {}).get("min_coverage", 0.8)):
            errors.append("BROKER_FUSION_COVERAGE_BELOW_MINIMUM")
        broker_status = str(fusion_manifest.get("Data_Quality_Status", "")).upper()
        if broker_status not in {"VALID", "PARTIAL_COVERAGE"}:
            errors.append(f"BROKER_FUSION_QUALITY_INVALID:{broker_status}")

    # Market artifacts are canonical inputs for Final Watchlist, even when the
    # old status file says delivery failed.
    try:
        from modules.job_runner.report_validation import validate_market_outlook_sources
        global_path = resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"
        regime_path = ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
        rotation_path = resolve(paths.get("sector_rotation_output", "data/output/market/SECTOR_ROTATION.json"))
        global_snapshot = read_json(global_path)
        market_status = read_json(regime_path)
        rotation = read_json(rotation_path)
        if str(rotation.get("trade_date", "")) != ctx.trade_date.isoformat():
            raise ValueError("SECTOR_ROTATION_DATE_MISMATCH")
        validate_market_outlook_sources(
            global_snapshot,
            market_status,
            rotation=rotation,
            input_paths=[global_path, regime_path, rotation_path],
        )
    except Exception as exc:
        errors.append(f"MARKET_CANONICAL_VALIDATION_FAILED:{type(exc).__name__}:{exc}")

    matched = fusion_manifest.get("broker_matched", 0) if fusion_manifest else 0
    expected = fusion_manifest.get("broker_expected", 0) if fusion_manifest else 0
    readiness = {
        "status": "READY" if not errors else "INVALID_DEPENDENCY",
        "broker_date": ctx.trade_date.isoformat(),
        "matched_symbols": matched,
        "expected_symbols": expected,
        "coverage_ratio": fusion_manifest.get("broker_coverage", 0.0) if fusion_manifest else 0.0,
        "snapshot_id": snapshot.get("snapshot_id", ""),
        "snapshot_trade_date": snapshot.get("trade_date", ""),
        "source": "CANONICAL_ARTIFACTS_PREVIEW",
    }
    return {
        "valid": not errors,
        "errors": errors,
        "canonical_artifacts": {
            "run_manifest": str(run_manifest_path),
            "fusion_manifest": str(fusion_manifest_path),
            "decision_source": str(required_outputs["fusion"]),
            "decision": str(required_outputs["decision"]),
            "entry_plans": str(required_outputs["entry_plans"]),
        },
        "broker_readiness": readiness,
    }


def job_pre_market(ctx) -> int:
    metadata = _source_details(ctx, record_type="MarketIndex")
    warnings = [] if metadata.get("provider_status") not in {"NOT_CONFIGURED", ""} else ["MARKET_INDEX_PRIMARY_NOT_CONFIGURED"]
    return _finish(ctx, "SUCCESS_WITH_WARNING" if warnings else "SUCCESS", "PRE_MARKET", EXIT_SUCCESS, {
        **metadata, "warnings": warnings, "snapshot_ids": {}, "dependency_status": {},
    })


def job_technical_snapshot(ctx) -> int:
    dependency = _require_integrated_dependencies(ctx, "technical_snapshot")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {
            "dependency_status": dependency, "errors": ["TECHNICAL_SNAPSHOT_DEPENDENCY_NOT_READY"],
        })
    snapshot = load_technical_snapshot(ctx)
    if snapshot.get("status") != "VALID":
        return _finish(ctx, "NOT_CONFIGURED", "TECHNICAL_SNAPSHOT", EXIT_WAITING_DATA, {
            "data_status": snapshot.get("status", "NOT_AVAILABLE"), "errors": [snapshot.get("reason", "SNAPSHOT_NOT_FOUND")],
            **_source_details(ctx, record_type="DailyBar"),
        })
    return _finish(ctx, "SUCCESS", "TECHNICAL_SNAPSHOT", EXIT_SUCCESS, {
        "snapshot_ids": {"technical": snapshot.get("snapshot_id", "")},
        "symbols_requested": snapshot.get("symbols_requested", snapshot.get("symbols_loaded", 0)),
        "symbols_loaded": snapshot.get("symbols_loaded", 0),
        "symbols_valid": snapshot.get("symbols_valid", 0),
        "symbols_failed": snapshot.get("symbols_failed", 0),
        "symbols_skipped": snapshot.get("symbols_skipped", 0),
        "snapshot_trade_date": snapshot.get("trade_date", ""),
        **_source_details(ctx, record_type="DailyBar"),
    })


def job_broker_summary(ctx) -> int:
    dependency = _require_integrated_dependencies(ctx, "broker_summary")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {"dependency_status": dependency})
    import_detail: dict[str, object] = {}
    if _official_runtime(ctx):
        # Reuse the Downloads bridge for a standalone Broker Summary run.  It
        # scans once for today's export and never waits for a future file.
        imported, import_detail = try_import_existing_broker_export(ctx)
        if not imported:
            return _finish(ctx, "WAITING_DATA", "BROKER_SOURCE_VALIDATION", EXIT_WAITING_DATA, {
                "data_status": "BROKER_EXPORT_NOT_READY",
                "broker_readiness": import_detail,
                "errors": ["BROKER_EXPORT_CURRENT_DATE_NOT_READY"],
            })
    path = ctx.path("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv")
    summary = ctx.path("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv")
    source_path = path if path.exists() else summary
    if not source_path.exists():
        return _finish(ctx, "NOT_CONFIGURED", "BROKER_SUMMARY", EXIT_WAITING_DATA, {
            "errors": ["BROKER_FILE_SOURCE_NOT_FOUND"], "data_status": "NOT_AVAILABLE", **_source_details(ctx, record_type="BrokerFlow"),
        })
    try:
        import pandas as pd
        frame = pd.read_csv(source_path, low_memory=False)
        symbols = frame.iloc[:, 0].dropna().astype(str).nunique() if not frame.empty else 0
    except Exception as exc:
        return _finish(ctx, "FAILED", "BROKER_SUMMARY", EXIT_FAILED, {"errors": [str(exc)], **_source_details(ctx, record_type="BrokerFlow")})
    if symbols <= 0:
        return _finish(ctx, "PARTIAL", "BROKER_SUMMARY", EXIT_WAITING_DATA, {"warnings": ["BROKER_FILE_EMPTY"], "symbols_loaded": 0, **_source_details(ctx, record_type="BrokerFlow")})
    fusion_details: dict = {}
    if _official_runtime(ctx):
        try:
            fusion = run_broker_fusion_from_snapshot(ctx)
            fusion_manifest = fusion.get("fusion_manifest", {})
            fusion_details = {
                "output_paths": {
                    "broker_summary_engine": str(fusion.get("decision_source", "")),
                    "broker_fusion_manifest": str(fusion.get("fusion_manifest_path", "")),
                },
                "broker_date": fusion_manifest.get("broker_date", ""),
                "broker_coverage": fusion_manifest.get("broker_coverage", 0.0),
                "data_quality_status": fusion_manifest.get("Data_Quality_Status", ""),
            }
        except Exception as exc:
            return _finish(ctx, "FAILED", "BROKER_SUMMARY_ENGINE", EXIT_FAILED, {
                "errors": [f"BROKER_FUSION_FAILED:{exc}"],
                **_source_details(ctx, record_type="BrokerFlow"),
            })
    fusion_quality = str(fusion_details.get("data_quality_status", "")).upper()
    broker_stage_status = "SUCCESS_WITH_WARNING" if fusion_quality == "PARTIAL_COVERAGE" or not os.getenv("STOCKBIT_API_KEY") else "SUCCESS"
    return _finish(ctx, broker_stage_status, "BROKER_SUMMARY", EXIT_SUCCESS, {
        "data_status": "FILE_FALLBACK" if not os.getenv("STOCKBIT_API_KEY") else "LIVE",
        "symbols_requested": symbols, "symbols_loaded": symbols, "symbols_valid": symbols,
        "warnings": ["STOCKBIT_API_NOT_CONFIGURED_FILE_FALLBACK"] if not os.getenv("STOCKBIT_API_KEY") else [],
        "broker_readiness": import_detail,
        **fusion_details,
        **_source_details(ctx, record_type="BrokerFlow"),
    })


def job_broker_multi_day(ctx) -> int:
    dependency = _require_integrated_dependencies(ctx, "broker_multi_day")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {"dependency_status": dependency})
    metadata = _source_details(ctx, record_type="BrokerFlow")
    if _official_runtime(ctx):
        try:
            result = run_broker_multiday_stage(ctx)
        except ReportSourceValidationError as exc:
            record_validation_error(ctx, exc)
            return _finish(ctx, "FAILED", "BROKER_MULTI_DAY_VALIDATION", EXIT_FAILED, {
                "report_type": exc.report_type,
                "errors": exc.errors,
                "input_paths": exc.input_paths,
                "source_of_truth": exc.source_of_truth,
                "details": exc.details,
            })
        except Exception as exc:
            return _finish(ctx, "FAILED", "BROKER_MULTI_DAY", EXIT_FAILED, {
                "errors": [f"BROKER_MULTI_DAY_ENGINE_FAILED:{exc}"],
                **metadata,
            })
        quality = str(result.get("data_quality_status", "")).upper()
        stage_status = "SUCCESS" if quality == "VALID" else "SUCCESS_WITH_WARNING"
        warnings = [] if quality == "VALID" else [
            f"MULTI_DAY_HISTORY_{quality or 'INSUFFICIENT_HISTORY'}: context tidak dipakai oleh Final Decision"
        ]
        return _finish(ctx, stage_status, "BROKER_MULTI_DAY", EXIT_SUCCESS, {
            **metadata,
            **result,
            "date_range": {
                "start": result.get("market_dates", [""])[0] if result.get("market_dates") else "",
                "end": result.get("market_dates", [ctx.trade_date.isoformat()])[-1] if result.get("market_dates") else ctx.trade_date.isoformat(),
            },
            "coverage_ratio": result.get("coverage_ratio", 0.0),
            "warnings": warnings,
        })
    return _finish(ctx, "NOT_CONFIGURED" if metadata.get("data_source_mode") == "NOT_CONFIGURED" else "SUCCESS_WITH_WARNING", "BROKER_MULTI_DAY", EXIT_SUCCESS, {
        **metadata,
        "date_range": {"start": "", "end": ctx.trade_date.isoformat()},
        "missing_days": [],
        "coverage_ratio": metadata.get("source_coverage_ratio", 0.0),
        "warnings": ["MULTI_DAY_HISTORY_NOT_LOADED"] if metadata.get("data_source_mode") == "NOT_CONFIGURED" else [],
    })


def job_universe_selection(ctx) -> int:
    dependency = _require_integrated_dependencies(ctx, "universe_selection")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {"dependency_status": dependency})
    return _finish(ctx, "SUCCESS", "UNIVERSE_SELECTION", EXIT_SUCCESS, {"snapshot_ids": {}, "symbols_requested": 0, "symbols_loaded": 0, "symbols_valid": 0, "warnings": ["UNIVERSE_SELECTION_DELEGATED_TO_TECHNICAL_STAGE"]})


def job_candidate_selection(ctx) -> int:
    dependency = _require_integrated_dependencies(ctx, "candidate_selection")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {"dependency_status": dependency})
    snapshot = load_technical_snapshot(ctx)
    candidates = snapshot.get("output_paths", {}).get("technical_candidates", "") if snapshot else ""
    return _finish(ctx, "SUCCESS" if candidates else "PARTIAL", "CANDIDATE_SELECTION", EXIT_SUCCESS, {"snapshot_ids": {"technical": snapshot.get("snapshot_id", "")} if snapshot else {}, "output_paths": {"candidates": candidates}, "warnings": [] if candidates else ["CANDIDATE_OUTPUT_NOT_FOUND"]})


def job_final_decision(ctx) -> int:
    dependency = _require_integrated_dependencies(ctx, "final_decision")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {"dependency_status": dependency})
    path = resolve("data/output/decision/FINAL_DECISION_V3.csv")
    if not path.exists():
        return _finish(ctx, "NOT_CONFIGURED", "FINAL_DECISION", EXIT_WAITING_DATA, {"errors": ["FINAL_DECISION_OUTPUT_NOT_FOUND"], "warnings": ["NO_DATA"]})
    try:
        import pandas as pd
        from modules.runtime.artifacts import write_artifact
        frame = pd.read_csv(path, low_memory=False)
        source_provenance = _source_details(ctx, record_type="BrokerFlow")
        snapshot_ids = {name: str(value.get("snapshot_id", "")) for name, value in _integrated_statuses(ctx).items() if value.get("snapshot_id")}
        candidates, errors = canonicalize_candidates(
            frame.to_dict(orient="records"),
            trade_date=ctx.trade_date.isoformat(),
            source_provenance=source_provenance,
            snapshot_ids=snapshot_ids or {"decision": ctx.run_id},
        )
        artifact_context = ctx.runtime_context
        artifact = write_artifact(
            artifact_context,
            "decisions",
            f"final_decision_{ctx.run_id}",
            [candidate.to_dict() for candidate in candidates],
            snapshot_id=str(snapshot_ids.get("decision", ctx.run_id)),
            source_metadata=source_provenance,
        )
        status = "SUCCESS_WITH_WARNING" if errors else "SUCCESS"
        details = {
            "output_paths": {"final_decision": str(path), "canonical_candidates": str(artifact)},
            "snapshot_ids": snapshot_ids,
            "source_provenance": source_provenance,
            "warnings": ["CANONICAL_CANDIDATE_VALIDATION_PARTIAL"] if errors else [],
            "errors": errors,
        }
        return _finish(ctx, status, "FINAL_DECISION", EXIT_SUCCESS, details)
    except Exception as exc:
        return _finish(ctx, "FAILED", "FINAL_DECISION", EXIT_FAILED, {"errors": [str(exc)], "output_paths": {"final_decision": str(path)}})


def job_telegram_delivery(ctx) -> int:
    configured = telegram_configured(ctx)
    return _finish(ctx, "SUCCESS" if configured else "NOT_CONFIGURED", "TELEGRAM_DELIVERY", EXIT_SUCCESS if configured else EXIT_SKIPPED, {
        "telegram_status": "READY" if configured else "NOT_CONFIGURED",
        "warnings": [] if configured else ["TELEGRAM_CREDENTIALS_EMPTY"],
    })


def job_status(ctx) -> int:
    return _finish(ctx, "SUCCESS", "JOB_STATUS", EXIT_SUCCESS, {"data_status": "STATUS_WRITER_READY"})


JOBS: dict[str, Callable] = {
    "market_outlook": job_market_outlook,
    "post_market": job_post_market,
    "final_watchlist": job_final_watchlist,
    "full_manual": job_full_manual,
    "pre_market": job_pre_market,
    "technical_snapshot": job_technical_snapshot,
    "broker_summary": job_broker_summary,
    "broker_multi_day": job_broker_multi_day,
    "universe_selection": job_universe_selection,
    "candidate_selection": job_candidate_selection,
    "final_decision": job_final_decision,
    "telegram_delivery": job_telegram_delivery,
    "job_status": job_status,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SDE Swing job runner")
    parser.add_argument("--job", choices=sorted(JOBS), required=True)
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preview-existing", action="store_true")
    parser.add_argument("--interactive-broker", action="store_true", help="Aktifkan broker break manual sebelum Final Watchlist")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--engine-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--reuse-yahoo-refresh", action="store_true", help="Reuse latest valid Yahoo refresh manifest")
    parser.add_argument("--parent-managed-lifecycle", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ctx = load_context(
        job=args.job,
        config_path=args.config,
        scheduler_config_path=args.scheduler_config,
        trade_date=args.trade_date or None,
        dry_run=args.dry_run,
        preview_existing=args.preview_existing,
        no_telegram=args.no_telegram,
        force=args.force,
        debug=args.debug,
        interactive_broker=args.interactive_broker,
    )
    if args.run_id:
        ctx.run_id = str(args.run_id)
    if args.engine_only:
        setattr(ctx, "_suppress_reports", True)
    if args.parent_managed_lifecycle:
        setattr(ctx, "_suppress_terminal_status", True)
    setattr(ctx, "reuse_yahoo_refresh", bool(args.reuse_yahoo_refresh))
    manifest_dir = resolve(ctx.config.get("paths", {}).get("manifest_dir", "data/output/manifests"))
    config_audit_path = manifest_dir / f"RUNTIME_CONFIG_{ctx.run_id}.json"
    write_runtime_config_audit(config_audit_path, ctx.config_provenance, ctx.config)
    ctx.config_provenance["audit_path"] = str(config_audit_path.resolve())
    write_status(ctx, "RUNNING", "START", EXIT_SUCCESS, {"config_audit_path": str(config_audit_path)})
    if ctx.job != "full_manual":
        is_trading, reason = trading_day_status(ctx)
        if not is_trading:
            return _finish(ctx, reason, "TRADING_CALENDAR", EXIT_SKIPPED, {"reason": reason})
    def execute_job() -> int:
        try:
            return JOBS[ctx.job](ctx)
        except ReportSourceValidationError as exc:
            record_validation_error(ctx, exc)
            return _finish(ctx, "FAILED", "REPORT_SOURCE_VALIDATION", EXIT_FAILED, {
                "report_type": exc.report_type,
                "errors": exc.errors,
                "input_paths": exc.input_paths,
                "source_of_truth": exc.source_of_truth,
                "details": exc.details,
            })
        except Exception as exc:
            trace_dir = resolve("data/output/job_status/tracebacks")
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_path = trace_dir / f"{ctx.run_id}.txt"
            rendered = traceback.format_exc()
            trace_path.write_text(rendered, encoding="utf-8")
            append_job_log(ctx, "UNHANDLED_JOB_EXCEPTION", rendered)
            if ctx.debug:
                print(rendered, file=sys.stderr, flush=True)
            return _finish(ctx, "FAILED", "EXCEPTION", EXIT_FAILED, {
                "error": str(exc), "errors": [str(exc)], "traceback_path": str(trace_path),
            })

    try:
        if args.parent_managed_lifecycle:
            return execute_job()
        with FileLock(ctx):
            try:
                needs_resource_lock = ctx.job in {"post_market", "final_watchlist", "full_manual"}
                if needs_resource_lock and ctx.scheduler_config.get("runtime", {}).get("global_resource_lock_enabled", True):
                    lock_name = ctx.scheduler_config.get("locks", {}).get("global_resource_lock_name", "sde_pipeline_write.lock")
                    with FileLock(ctx, str(lock_name), kind="global_resource"):
                        return execute_job()
                return execute_job()
            except ResourceLocked as exc:
                return _finish(ctx, exc.status, "GLOBAL_RESOURCE_LOCK", EXIT_RESOURCE_LOCKED, {"error": str(exc), "global_resource_lock_status": "BUSY"})
    except JobAlreadyRunning as exc:
        return _finish(ctx, exc.status, "LOCK", EXIT_SKIPPED, {"error": str(exc), "lock_status": "BUSY"})
    except Exception as exc:
        trace_dir = resolve("data/output/job_status/tracebacks")
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / f"{ctx.run_id}.txt"
        rendered = traceback.format_exc()
        trace_path.write_text(rendered, encoding="utf-8")
        append_job_log(ctx, "LOCK_BOUNDARY_EXCEPTION", rendered)
        return _finish(ctx, "FAILED", "EXCEPTION", EXIT_FAILED, {"error": str(exc), "errors": [str(exc)], "traceback_path": str(trace_path)})


if __name__ == "__main__":
    raise SystemExit(main())
