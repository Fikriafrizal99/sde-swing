#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
)


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
        "preview_paths": [str(p) for p in preview_paths],
        "delivery": delivery,
        **_delivery_summary(delivery),
        "source_run_id": manifest.get("Run_ID", ctx.run_id) if manifest else "EXISTING_SNAPSHOT",
    })


def job_final_watchlist(ctx) -> int:
    print("[1/6] Memeriksa snapshot teknikal dan Broker Summary...", flush=True)
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


JOBS: dict[str, Callable] = {
    "market_outlook": job_market_outlook,
    "post_market": job_post_market,
    "final_watchlist": job_final_watchlist,
    "full_manual": job_full_manual,
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
    write_status(ctx, "RUNNING", "START", EXIT_SUCCESS)
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
