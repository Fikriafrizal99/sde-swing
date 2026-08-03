#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import Callable

from modules.global_market.global_market_snapshot import build_global_market_snapshot
from modules.market_data.market_outlook_regime import calculate_market_outlook_regime, save_market_outlook_regime
from modules.job_runner.core import (
    broker_readiness,
    load_technical_snapshot,
    run_interactive_broker_break,
    run_command,
    run_post_market_technical_stage,
    run_final_from_snapshot,
    run_master_pipeline,
    sync_outcome_tracker,
    wait_for_broker_ready,
)
from modules.job_runner.delivery import deliver
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
    read_json,
)
from modules.decision.adapter import canonicalize_candidates
from modules.runtime.jobs import INTEGRATED_JOB_NAMES, JOB_DEPENDENCIES, validate_dependency_status


def _finish(ctx, status: str, stage: str, code: int, details: dict | None = None) -> int:
    write_status(ctx, status, stage, code, details)
    return code


def _status_after_delivery(delivery: list[dict]) -> str:
    if any(item.get("status") == "FAILED" for item in delivery):
        return "DELIVERY_FAILED"
    if delivery and all(item.get("status") == "DUPLICATE_SUPPRESSED" for item in delivery):
        return "DUPLICATE_SUPPRESSED"
    return "SUCCESS"


def _exit_after_delivery(ctx, delivery: list[dict]) -> int:
    if any(item.get("status") == "FAILED" for item in delivery):
        return EXIT_DELIVERY_FAILED
    if delivery and all(item.get("status") == "DUPLICATE_SUPPRESSED" for item in delivery):
        return EXIT_DUPLICATE
    return EXIT_SUCCESS


def _delivery_summary(delivery: list[dict]) -> dict:
    ids: list = []
    for item in delivery:
        if item.get("telegram_message_ids"):
            ids.extend(item.get("telegram_message_ids", []))
        elif item.get("telegram_message_id"):
            ids.append(item.get("telegram_message_id"))
    return {
        "telegram_status": "FAILED" if any(item.get("status") == "FAILED" for item in delivery) else ("SKIPPED" if all(item.get("status") in {"DRY_RUN", "NO_TELEGRAM", "DUPLICATE_SUPPRESSED"} for item in delivery) else "SENT"),
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
        "Warnings": warnings,
        "Output_Files": snapshot.get("output_paths", {}),
    }


def _market_source_details(ctx, snapshot: dict) -> dict:
    manager = _source_details(ctx, record_type="MarketIndex")
    # Global Market has its own historical Yahoo provider; preserve that
    # provider's truthful mode while retaining manager readiness/health.
    manager.update({
        "primary_provider": snapshot.get("provider") or manager.get("primary_provider", "NOT_CONFIGURED"),
        "provider_status": snapshot.get("provider") or manager.get("provider_status", "NOT_CONFIGURED"),
        "data_source_mode": snapshot.get("source_mode") or manager.get("data_source_mode", "NOT_CONFIGURED"),
        "source_coverage_ratio": snapshot.get("coverage_ratio", manager.get("source_coverage_ratio", 0.0)),
    })
    return manager


def job_market_outlook(ctx) -> int:
    print("[1/5] Menyiapkan snapshot global market...", flush=True)
    global_snapshot = build_global_market_snapshot(ctx, fallback_to_existing_on_failure=True)
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

    payloads = market_outlook_payload(ctx, global_snapshot, market_status=market_status)
    preview_paths = write_payloads(ctx, payloads)
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
            },
            "preview_paths": [str(p) for p in preview_paths],
            "telegram_status": "NOT_SENT_INVALID_DATA",
            "minimum_required_coverage_ratio": minimum_coverage,
        })
    print("[5/5] Mengirim Market Outlook ke Telegram...", flush=True)
    delivery = deliver(ctx, payloads)
    sentiment = global_snapshot.get("global_sentiment", {}) if global_snapshot else {}
    code = _exit_after_delivery(ctx, delivery)
    return _finish(ctx, _status_after_delivery(delivery), "MARKET_OUTLOOK", code, {
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
        "warnings": [*global_snapshot.get("warnings", []), *market_status.get("warnings", [])],
        "errors": global_snapshot.get("errors", []),
        "output_paths": {
            "global_market_snapshot": str(resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"),
            "market_outlook_regime": str(regime_output),
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
        except Exception as exc:
            allow_fallback = bool(ctx.scheduler_config.get("post_market", {}).get("fallback_to_existing_on_refresh_failure", True))
            snapshot = load_technical_snapshot(ctx) if allow_fallback else {}
            if allow_fallback and snapshot.get("status") == "VALID":
                warning = f"REFRESH_FAILED_USING_EXISTING_SNAPSHOT: {exc}"
                append_job_log(ctx, "POST_MARKET_REFRESH_FALLBACK", warning)
                print("[fallback] Refresh gagal; snapshot teknikal existing hari ini dipakai.", flush=True)
                manifest = _manifest_from_existing_snapshot(ctx, snapshot, warning=warning)
            else:
                raise
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
            "provider_status": manifest.get("provider_status") or manifest.get("source_metadata", {}).get("provider_status", "NOT_CONFIGURED"),
            "data_source_mode": manifest.get("data_source_mode") or manifest.get("source_metadata", {}).get("data_source_mode", "NOT_CONFIGURED"),
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
    payloads = post_market_payloads(ctx, manifest)
    preview_paths = write_payloads(ctx, payloads)
    print(f"[3/4] Preview dibuat: {len(preview_paths)} file", flush=True)
    print("[4/4] Mengirim ringkasan Post Market ke Telegram...", flush=True)
    delivery = deliver(ctx, payloads)
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
        "provider_status": manifest.get("provider_status") or manifest.get("source_metadata", {}).get("provider_status", "NOT_CONFIGURED"),
        "data_source_mode": manifest.get("data_source_mode") or manifest.get("source_metadata", {}).get("data_source_mode", "NOT_CONFIGURED"),
        "source_coverage_ratio": manifest.get("source_coverage_ratio", 0.0),
        "snapshot_ids": manifest.get("snapshot_ids", {"technical": manifest.get("Snapshot_ID", "")}),
        "warnings": manifest.get("Warnings", []),
        "preview_paths": [str(p) for p in preview_paths],
        "delivery": delivery,
        **_delivery_summary(delivery),
        "source_run_id": manifest.get("Run_ID", ctx.run_id) if manifest else "EXISTING_SNAPSHOT",
    })


def job_final_watchlist(ctx) -> int:
    print("[1/6] Memeriksa snapshot teknikal dan Broker Summary...", flush=True)
    dependency = _require_integrated_dependencies(ctx, "final_watchlist")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {
            "dependency_status": dependency,
            "errors": ["FINAL_WATCHLIST_DEPENDENCY_NOT_READY"],
            "warnings": ["Dependencies must match trade_date, run status, and config_version."],
        })
    if ctx.interactive_broker:
        ready, detail = broker_readiness(ctx)
        if not ready and detail.get("status") not in {"STALE_TECHNICAL_SNAPSHOT", "INVALID_DEPENDENCY"}:
            print("[BROKER] Broker Summary hari ini belum siap. Memulai broker break manual...", flush=True)
            ready, detail = run_interactive_broker_break(ctx, detail)
    else:
        ready, detail = wait_for_broker_ready(ctx)
    if not ready:
        policy = str(ctx.scheduler_config.get("final_watchlist", {}).get("missing_broker_policy", "skip_final_watchlist"))
        if policy == "send_preliminary_watchlist" and detail.get("status") not in {"STALE_TECHNICAL_SNAPSHOT", "INVALID_DEPENDENCY"}:
            payloads = broker_waiting_payload(ctx, detail) + preliminary_watchlist_payloads(ctx, detail)
            preview_paths = write_payloads(ctx, payloads)
            delivery = deliver(ctx, payloads)
            return _finish(ctx, "PARTIAL", "PRELIMINARY_WATCHLIST", EXIT_SUCCESS, {
                **detail,
                "broker_readiness_status": detail.get("status", detail.get("reason", "")),
                "preview_paths": [str(p) for p in preview_paths],
                "delivery": delivery,
                **_delivery_summary(delivery),
                "warnings": ["PRELIMINARY_ONLY; broker summary belum valid"],
            })
        payloads = broker_waiting_payload(ctx, detail)
        preview_paths = write_payloads(ctx, payloads)
        delivery = deliver(ctx, payloads)
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
        except Exception as exc:
            warning = f"OUTCOME_TRACKER_WARNING: {exc}"
            append_job_log(ctx, "OUTCOME_TRACKER_WARNING", warning)
            manifest.setdefault("Warnings", []).append(warning)
            print(f"[analytics warning] {exc}", flush=True)
    print("[4/6] Membentuk Final Watchlist dan detail broker kandidat...", flush=True)
    payloads = final_watchlist_payloads(ctx, manifest)
    preview_paths = write_payloads(ctx, payloads)
    print(f"[5/6] Preview dibuat: {len(preview_paths)} file", flush=True)
    print("[6/6] Mengirim Final Watchlist ke Telegram...", flush=True)
    delivery = deliver(ctx, payloads)
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
        return {
            "primary_provider": "NOT_CONFIGURED",
            "provider_status": "NOT_CONFIGURED",
            "data_source_mode": "NOT_CONFIGURED",
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
    return statuses


def _require_integrated_dependencies(ctx, job_name: str) -> dict | None:
    # Hand-built legacy test contexts do not carry config provenance. They keep
    # the proven Stage 1/2 behaviour; official load_context runs are strict.
    if str(ctx.config_provenance.get("config_version", "")) != "1.7.0-multisource":
        return None
    check = validate_dependency_status(ctx.runtime_context, job_name, _integrated_statuses(ctx))
    if check.get("valid"):
        return None
    return check


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
    return _finish(ctx, "SUCCESS_WITH_WARNING" if not os.getenv("STOCKBIT_API_KEY") else "SUCCESS", "BROKER_SUMMARY", EXIT_SUCCESS, {
        "data_status": "FILE_FALLBACK" if not os.getenv("STOCKBIT_API_KEY") else "LIVE",
        "symbols_requested": symbols, "symbols_loaded": symbols, "symbols_valid": symbols,
        "warnings": ["STOCKBIT_API_NOT_CONFIGURED_FILE_FALLBACK"] if not os.getenv("STOCKBIT_API_KEY") else [],
        **_source_details(ctx, record_type="BrokerFlow"),
    })


def job_broker_multi_day(ctx) -> int:
    dependency = _require_integrated_dependencies(ctx, "broker_multi_day")
    if dependency:
        return _finish(ctx, "SKIPPED", "DEPENDENCY_VALIDATION", EXIT_SKIPPED, {"dependency_status": dependency})
    metadata = _source_details(ctx, record_type="BrokerFlow")
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
    configured = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
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
    manifest_dir = resolve(ctx.config.get("paths", {}).get("manifest_dir", "data/output/manifests"))
    config_audit_path = manifest_dir / f"RUNTIME_CONFIG_{ctx.run_id}.json"
    write_runtime_config_audit(config_audit_path, ctx.config_provenance, ctx.config)
    ctx.config_provenance["audit_path"] = str(config_audit_path.resolve())
    write_status(ctx, "RUNNING", "START", EXIT_SUCCESS, {"config_audit_path": str(config_audit_path)})
    if ctx.job != "full_manual":
        is_trading, reason = trading_day_status(ctx)
        if not is_trading:
            return _finish(ctx, reason, "TRADING_CALENDAR", EXIT_SKIPPED, {"reason": reason})
    try:
        with FileLock(ctx):
            needs_resource_lock = ctx.job in {"post_market", "final_watchlist", "full_manual"}
            if needs_resource_lock and ctx.scheduler_config.get("runtime", {}).get("global_resource_lock_enabled", True):
                lock_name = ctx.scheduler_config.get("locks", {}).get("global_resource_lock_name", "sde_pipeline_write.lock")
                with FileLock(ctx, str(lock_name), kind="global_resource"):
                    return JOBS[ctx.job](ctx)
            return JOBS[ctx.job](ctx)
    except ResourceLocked as exc:
        return _finish(ctx, exc.status, "GLOBAL_RESOURCE_LOCK", EXIT_RESOURCE_LOCKED, {"error": str(exc), "global_resource_lock_status": "BUSY"})
    except JobAlreadyRunning as exc:
        return _finish(ctx, exc.status, "LOCK", EXIT_SKIPPED, {"error": str(exc), "lock_status": "BUSY"})
    except Exception as exc:
        if ctx.debug:
            raise
        trace_dir = resolve("data/output/job_status/tracebacks")
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / f"{ctx.run_id}.txt"
        trace_path.write_text(traceback.format_exc(), encoding="utf-8")
        return _finish(ctx, "FAILED", "EXCEPTION", EXIT_FAILED, {"error": str(exc), "errors": [str(exc)], "traceback_path": str(trace_path)})


if __name__ == "__main__":
    raise SystemExit(main())
