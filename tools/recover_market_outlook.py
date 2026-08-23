#!/usr/bin/env python3
from __future__ import annotations

"""Recover a missed Market Outlook with point-in-time historical inputs.

The normal Market Outlook runner is intentionally not modified.  This tool
reuses the current global sentiment, IHSG regime, sector-rotation, enhanced
report builder, and status contracts while replacing only acquisition context
with historical/as-of inputs.  Telegram is always disabled during recovery.
"""

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.global_market.historical_global_market_snapshot import (
    build_historical_global_market_snapshot,
)
from modules.job_runner.enhanced_runtime_bridge import market_outlook_payloads
from modules.job_runner.report_validation import ReportSourceValidationError
from modules.job_runner.reports import write_payloads
from modules.job_runner.runtime import (
    EXIT_FAILED,
    EXIT_SUCCESS,
    FileLock,
    JobAlreadyRunning,
    append_job_log,
    load_context,
    read_json,
    resolve,
    write_status,
)
from modules.market_data.market_outlook_regime import (
    calculate_market_outlook_regime,
    save_market_outlook_regime,
)
from modules.market_data.sector_rotation import produce_sector_rotation
from tools.resolve_last_trading_day import (
    DEFAULT_DATA_READY_TIME,
    DEFAULT_TIMEZONE,
    is_trading_day,
    resolve_last_completed_trading_day,
    resolve_last_trading_day,
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _clock(value: str, fallback: str = "07:30") -> time:
    raw = str(value or fallback).strip()
    try:
        hour, minute = raw.split(":", 1)
        return time(int(hour), int(minute))
    except Exception:
        hour, minute = fallback.split(":", 1)
        return time(int(hour), int(minute))


def _target_trade_date(
    explicit: str,
    *,
    calendar: dict[str, Any],
    scheduler: dict[str, Any],
    now: datetime,
) -> date:
    if explicit:
        requested = date.fromisoformat(explicit)
        return resolve_last_trading_day(requested, calendar)
    post_cfg = scheduler.get("post_market", {}) if isinstance(scheduler.get("post_market", {}), dict) else {}
    ready_time = str(post_cfg.get("time") or DEFAULT_DATA_READY_TIME)
    return resolve_last_completed_trading_day(now, calendar, data_ready_time=ready_time)


def _previous_idx_session(target: date, calendar: dict[str, Any]) -> date:
    return resolve_last_trading_day(target - timedelta(days=1), calendar)


def _historical_as_of(target: date, scheduler: dict[str, Any]) -> datetime:
    timezone_name = str(scheduler.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    outlook_cfg = scheduler.get("market_outlook", {}) if isinstance(scheduler.get("market_outlook", {}), dict) else {}
    outlook_time = _clock(str(outlook_cfg.get("time") or "07:30"))
    return datetime.combine(target, outlook_time, tzinfo=ZoneInfo(timezone_name))


def _resolve_snapshot_file(raw: Any) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return resolve(path)


def _previous_technical_source(previous_session: date) -> tuple[Path, Path, dict[str, Any]]:
    snapshot_index = resolve("data/output/snapshots") / previous_session.isoformat() / "latest_snapshot.json"
    snapshot = _load_json(snapshot_index)
    if not snapshot:
        raise RuntimeError(
            f"MARKET_OUTLOOK_RECOVERY_PREVIOUS_TECHNICAL_SNAPSHOT_NOT_FOUND:{previous_session.isoformat()}"
        )
    snapshot_date = str(snapshot.get("trade_date") or "")[:10]
    if snapshot_date != previous_session.isoformat():
        raise RuntimeError(
            f"MARKET_OUTLOOK_RECOVERY_TECHNICAL_DATE_MISMATCH:{snapshot_date}!={previous_session.isoformat()}"
        )
    outputs = snapshot.get("output_paths") if isinstance(snapshot.get("output_paths"), dict) else {}
    technical = _resolve_snapshot_file(outputs.get("technical_features"))
    if technical is None or not technical.exists() or technical.stat().st_size <= 0:
        raise RuntimeError(
            f"MARKET_OUTLOOK_RECOVERY_TECHNICAL_FEATURES_NOT_FOUND:{previous_session.isoformat()}"
        )
    return technical, snapshot_index, snapshot


def _refresh_ihsg_best_effort(ctx) -> str:
    ihsg_path = ctx.path("ihsg_csv", "data/input/IHSG.csv")
    if not ihsg_path.exists():
        ihsg_path.parent.mkdir(parents=True, exist_ok=True)
    updater = ctx.path("ihsg_updater", "modules/market_data/update_ihsg.py")
    command = [
        sys.executable,
        "-u",
        str(updater),
        "--output",
        str(ihsg_path),
        "--period",
        str(ctx.config.get("download", {}).get("period", "2y")),
    ]
    try:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            warning = f"IHSG_REFRESH_FALLBACK_EXIT_{completed.returncode}"
            append_job_log(ctx, "MARKET_OUTLOOK_RECOVERY_IHSG_FALLBACK", warning)
            return warning
    except Exception as exc:
        warning = f"IHSG_REFRESH_FALLBACK:{type(exc).__name__}:{exc}"
        append_job_log(ctx, "MARKET_OUTLOOK_RECOVERY_IHSG_FALLBACK", warning)
        return warning
    return ""


def _historical_zapi_status() -> dict[str, Any]:
    # ZAPI exchange activity is current-state data and no point-in-time endpoint
    # is guaranteed here.  Recovery therefore refuses to query it and records
    # the omission explicitly rather than leaking weekend/current knowledge into
    # a historical pre-market report.
    return {
        "zapi_status": "SKIPPED_HISTORICAL_AS_OF",
        "zapi_request_count": 0,
        "zapi_request_cap": 0,
        "metadata_cache_status": "NOT_USED_HISTORICAL_AS_OF",
        "metadata_cache_date": "",
        "market_activity_cache_status": "NOT_USED_HISTORICAL_AS_OF",
        "suspended_count": 0,
        "uma_count": 0,
        "relisting_count": 0,
        "zapi_degraded": True,
        "zapi_degraded_reason": "POINT_IN_TIME_ZAPI_ACTIVITY_NOT_QUERIED",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover missed Market Outlook using historical/as-of data without Telegram"
    )
    parser.add_argument("--trade-date", default="", help="Target trading date YYYY-MM-DD; default latest completed IDX session")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--force", action="store_true", help="Rebuild even when a reusable authentic/recovery snapshot exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scheduler_path = resolve(args.scheduler_config)
    scheduler = _load_json(scheduler_path)
    calendar_path = resolve(scheduler.get("trading_calendar", "config/trading_calendar.json"))
    calendar = _load_json(calendar_path)
    if not calendar:
        print("[FAILED] Trading calendar tidak tersedia/invalid.", file=sys.stderr)
        return EXIT_FAILED

    timezone_name = str(scheduler.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    now = datetime.now(ZoneInfo(timezone_name))
    try:
        target = _target_trade_date(args.trade_date, calendar=calendar, scheduler=scheduler, now=now)
        if not is_trading_day(target, calendar):
            raise RuntimeError(f"TARGET_NOT_TRADING_DAY:{target.isoformat()}")
        as_of_at = _historical_as_of(target, scheduler)
        previous_session = _previous_idx_session(target, calendar)
    except Exception as exc:
        print(f"[FAILED] Gagal menentukan recovery context: {exc}", file=sys.stderr)
        return EXIT_FAILED

    ctx = load_context(
        job="market_outlook",
        config_path=args.config,
        scheduler_config_path=args.scheduler_config,
        trade_date=target.isoformat(),
        dry_run=False,
        preview_existing=False,
        no_telegram=True,
        force=bool(args.force),
        debug=False,
        interactive_broker=False,
    )
    setattr(ctx, "historical_recovery", True)
    setattr(ctx, "historical_as_of", as_of_at.isoformat(timespec="seconds"))

    print("=" * 68)
    print(" MARKET OUTLOOK RECOVERY - HISTORICAL / AS-OF")
    print("=" * 68)
    print(f"Target trade date : {target.isoformat()}")
    print(f"As-of timestamp   : {as_of_at.isoformat(timespec='minutes')}")
    print(f"IDX context date  : {previous_session.isoformat()}")
    print("Telegram          : OFF")
    print("Quant/decision    : UNCHANGED / NOT EXECUTED")
    print()

    write_status(ctx, "RUNNING", "MARKET_OUTLOOK_RECOVERY_START", EXIT_SUCCESS, {
        "engine_status": "RUNNING",
        "report_status": "NOT_RUN",
        "delivery_status": "NOT_RUN",
        "recovery_mode": "HISTORICAL_AS_OF",
        "historical_as_of": as_of_at.isoformat(timespec="seconds"),
        "market_context_date": previous_session.isoformat(),
        "warnings": ["RECOVERY_MODE; Telegram disabled; decision/quant engine not executed."],
    })

    try:
        with FileLock(ctx):
            technical_path, technical_snapshot_path, technical_snapshot = _previous_technical_source(previous_session)
            append_job_log(ctx, "MARKET_OUTLOOK_RECOVERY_TECHNICAL_SOURCE", str(technical_snapshot_path))

            global_snapshot = build_historical_global_market_snapshot(
                ctx,
                as_of_at=as_of_at,
                force=bool(args.force),
            )
            coverage = float(global_snapshot.get("coverage_ratio", 0.0) or 0.0)
            minimum = float(global_snapshot.get("minimum_required_coverage_ratio", 0.5) or 0.5)
            if coverage < minimum:
                raise RuntimeError(
                    f"MARKET_OUTLOOK_RECOVERY_GLOBAL_COVERAGE:{coverage:.4f}<{minimum:.4f}"
                )

            ihsg_warning = _refresh_ihsg_best_effort(ctx)
            ihsg_path = ctx.path("ihsg_csv", "data/input/IHSG.csv")
            market_status = calculate_market_outlook_regime(
                ihsg_path=ihsg_path,
                technical_path=technical_path,
                as_of_date=previous_session,
            )
            if market_status.get("market_regime") in {None, "", "UNKNOWN"}:
                raise RuntimeError(
                    f"MARKET_OUTLOOK_RECOVERY_IHSG_REGIME_UNAVAILABLE:{previous_session.isoformat()}"
                )

            rotation_output = (
                ctx.previews_root.parent
                / "market_regime"
                / target.isoformat()
                / "sector_rotation_recovery.json"
            )
            metadata_text = str(ctx.config.get("paths", {}).get("sector_rotation_metadata", "")).strip()
            metadata_path = resolve(metadata_text) if metadata_text else None
            rotation = produce_sector_rotation(
                technical_path,
                rotation_output,
                previous_session,
                metadata_path=metadata_path,
            )

            market_status.update({
                "provider": global_snapshot.get("provider", "YAHOO"),
                "source_mode": "HISTORICAL_AS_OF",
                "coverage": coverage,
                "historical_recovery": True,
                "historical_as_of": as_of_at.isoformat(timespec="seconds"),
                "market_context_date": previous_session.isoformat(),
                "sector_rotation_path": str(rotation_output),
                "sector_rotation_status": rotation.get("status", "INSUFFICIENT_DATA"),
                "sector_rotation_coverage": rotation.get("coverage", 0.0),
                **_historical_zapi_status(),
            })
            warnings = list(market_status.get("warnings", []) or [])
            warnings.append("MARKET_OUTLOOK_HISTORICAL_AS_OF_RECOVERY")
            warnings.append("ZAPI_POINT_IN_TIME_ACTIVITY_SKIPPED")
            if ihsg_warning:
                warnings.append(ihsg_warning)
            market_status["warnings"] = warnings

            regime_path = (
                ctx.previews_root.parent
                / "market_regime"
                / target.isoformat()
                / "market_outlook_regime.json"
            )
            save_market_outlook_regime(market_status, regime_path)

            payloads = market_outlook_payloads(ctx, global_snapshot, market_status)
            preview_paths = write_payloads(ctx, payloads)
            if not payloads or not preview_paths:
                raise RuntimeError("MARKET_OUTLOOK_RECOVERY_PAYLOAD_EMPTY")

            write_status(ctx, "SUCCESS_WITH_WARNING", "MARKET_OUTLOOK_RECOVERY_COMPLETE", EXIT_SUCCESS, {
                "engine_status": "SUCCESS_WITH_WARNING",
                "report_status": "SUCCESS",
                "delivery_status": "NOT_RUN",
                "telegram_status": "SKIPPED",
                "recovery_mode": "HISTORICAL_AS_OF",
                "historical_as_of": as_of_at.isoformat(timespec="seconds"),
                "market_context_date": previous_session.isoformat(),
                "snapshot_id": global_snapshot.get("snapshot_id", ""),
                "global_market_snapshot_id": global_snapshot.get("snapshot_id", ""),
                "global_market_coverage_ratio": coverage,
                "market_regime": market_status.get("market_regime", ""),
                "market_regime_data_date": market_status.get("data_date", ""),
                "data_source_mode": "HISTORICAL_AS_OF",
                "provider_status": global_snapshot.get("source_metadata", {}).get("provider_status", ""),
                "preview_paths": [str(path) for path in preview_paths],
                "output_paths": {
                    "global_market_snapshot": str(resolve("data/output/global_market") / target.isoformat() / "global_market_snapshot.json"),
                    "market_outlook_regime": str(regime_path),
                    "sector_rotation": str(rotation_output),
                    "previous_technical_snapshot": str(technical_snapshot_path),
                    "previous_technical_features": str(technical_path),
                },
                "warnings": warnings,
                "errors": [],
                "source_technical_snapshot_id": technical_snapshot.get("snapshot_id", ""),
            })
            print(f"[OK] Market Outlook recovery selesai untuk {target.isoformat()}.")
            print("     Gunakan Preview Existing untuk inspeksi; Kirim Ulang bila hasil sudah sesuai.")
            return EXIT_SUCCESS

    except ReportSourceValidationError as exc:
        errors = list(exc.errors)
        print("[FAILED] Report recovery tidak lolos source validation:", "; ".join(errors), file=sys.stderr)
        write_status(ctx, "FAILED", "MARKET_OUTLOOK_RECOVERY_SOURCE_VALIDATION", EXIT_FAILED, {
            "engine_status": "FAILED",
            "report_status": "FAILED",
            "delivery_status": "NOT_RUN",
            "recovery_mode": "HISTORICAL_AS_OF",
            "historical_as_of": as_of_at.isoformat(timespec="seconds"),
            "market_context_date": previous_session.isoformat(),
            "errors": errors,
            "warnings": ["Recovery fail-closed; Telegram tidak dijalankan."],
        })
        return EXIT_FAILED
    except (JobAlreadyRunning, RuntimeError, Exception) as exc:
        print(f"[FAILED] Market Outlook recovery: {type(exc).__name__}: {exc}", file=sys.stderr)
        write_status(ctx, "FAILED", "MARKET_OUTLOOK_RECOVERY_FAILED", EXIT_FAILED, {
            "engine_status": "FAILED",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "recovery_mode": "HISTORICAL_AS_OF",
            "historical_as_of": as_of_at.isoformat(timespec="seconds"),
            "market_context_date": previous_session.isoformat(),
            "errors": [f"{type(exc).__name__}: {exc}"],
            "warnings": ["Recovery fail-closed; Telegram tidak dijalankan."],
        })
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
