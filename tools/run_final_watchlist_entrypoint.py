#!/usr/bin/env python3
from __future__ import annotations

"""Resolve the canonical Final Watchlist trade date before broker-period orchestration.

This entrypoint restores the lifecycle contract for manual Final Watchlist runs:
calendar dates are resolved to the latest completed IDX session, while failures
that happen before the final_watchlist engine can write status are terminalized
instead of leaving an older SUCCESS status looking current.

After the official Final Watchlist child has completed successfully, the
canonical Active Recommendations card is sent as a non-blocking presentation
step, then the isolated Watchlist AI runner is invoked. Neither downstream step
can replace the official Final Watchlist exit status.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from swing_utils import make_run_id
from tools.resolve_last_trading_day import (
    DEFAULT_DATA_READY_TIME,
    DEFAULT_TIMEZONE,
    resolve_last_completed_trading_day,
)

STATUS_PATH = ROOT / "data/output/job_status/final_watchlist_latest.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _arg_value(argv: list[str], name: str) -> str:
    try:
        index = argv.index(name)
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    return str(argv[index + 1]).strip()


def resolve_effective_trade_date(argv: list[str], *, now: datetime | None = None) -> str:
    explicit = _arg_value(argv, "--trade-date")
    if explicit:
        return explicit

    calendar = read_json(ROOT / "config/trading_calendar.json")
    if not calendar:
        raise RuntimeError("TRADING_CALENDAR_INVALID")

    scheduler_path_text = _arg_value(argv, "--scheduler-config") or "config/scheduler.json"
    scheduler_path = Path(scheduler_path_text)
    if not scheduler_path.is_absolute():
        scheduler_path = ROOT / scheduler_path
    scheduler = read_json(scheduler_path)

    timezone_name = str(scheduler.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    broker_cfg = scheduler.get("broker_portfolio", {}) or {}
    post_market_cfg = scheduler.get("post_market", {}) or {}
    ready_time = str(
        broker_cfg.get("data_ready_time")
        or post_market_cfg.get("time")
        or DEFAULT_DATA_READY_TIME
    ).strip()

    current = now or datetime.now(ZoneInfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        current = current.astimezone(ZoneInfo(timezone_name))

    return resolve_last_completed_trading_day(
        current,
        calendar,
        data_ready_time=ready_time,
    ).isoformat()


def _status_fingerprint(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def write_orchestration_failure(
    *,
    trade_date: str,
    exit_code: int,
    started_at: datetime,
    previous_status: dict[str, Any],
    observed_status: dict[str, Any],
) -> None:
    observed_changed = _status_fingerprint(observed_status) != _status_fingerprint(previous_status)
    observed_state = str(observed_status.get("status", "")).upper()

    # Keep a richer current terminal failure written by the engine/runtime itself.
    # RUNNING is not terminal; a child crash must still be terminalized here.
    if observed_changed and observed_state not in {"", "SUCCESS", "SUCCESS_WITH_WARNING", "RUNNING"}:
        return

    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    previous_run_id = str(previous_status.get("run_id", "")).strip()
    previous_trade_date = str(previous_status.get("trade_date", "")).strip()

    if observed_changed and observed_state in {"SUCCESS", "SUCCESS_WITH_WARNING"}:
        reason = "FINAL_WATCHLIST_ORCHESTRATION_FAILED_AFTER_ENGINE_STATUS"
        engine_status = str(
            observed_status.get("engine_status")
            or (observed_status.get("details", {}) or {}).get("engine_status")
            or observed_state
        )
    else:
        reason = "FINAL_WATCHLIST_ORCHESTRATION_FAILED_BEFORE_STATUS_UPDATE"
        engine_status = "NOT_RUN"

    payload: dict[str, Any] = {
        "run_id": make_run_id(),
        "job": "final_watchlist",
        "status_channel": "ENGINE",
        "status": "FAILED",
        "status_v1_7": "FAILED",
        "legacy_status": "FAILED",
        "engine_status": engine_status,
        "delivery_status": "NOT_RUN",
        "process_status": "FAILED",
        "trade_date": trade_date,
        "job_mode": "LIVE",
        "current_stage": "FINAL_WATCHLIST_ORCHESTRATION",
        "exit_code": int(exit_code),
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "warnings": [],
        "errors": [reason],
        "details": {
            "engine_status": engine_status,
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "reason": reason,
            "effective_trade_date": trade_date,
            "previous_status_run_id": previous_run_id,
            "previous_status_trade_date": previous_trade_date,
            "child_exit_code": int(exit_code),
        },
    }
    atomic_write_json(STATUS_PATH, payload)


def _run_active_recommendations(forwarded: list[str]) -> None:
    """Send the canonical Active Recommendations card after Final Watchlist.

    The live Final Watchlist child already refreshed the outcome tracker and
    produced ``ACTIVE_RECOMMENDATIONS.csv``. This step only renders/sends that
    fresh analytics state. Dry-run is skipped because outcome sync is
    intentionally not persisted there; using an older CSV would be misleading.
    Failures are presentation-only and cannot alter the official Final
    Watchlist status.
    """
    if "--no-telegram" in forwarded:
        print("[LIFECYCLE] Active Recommendations skipped: --no-telegram.", flush=True)
        return
    if "--dry-run" in forwarded:
        print("[LIFECYCLE] Active Recommendations skipped: --dry-run has no fresh persisted lifecycle state.", flush=True)
        return

    command = [
        sys.executable,
        "-u",
        str(ROOT / "tools/send_active_recommendations.py"),
    ]
    for option in ("--telegram-config", "--scheduler-config"):
        value = _arg_value(forwarded, option)
        if value:
            command.extend([option, value])

    try:
        completed = subprocess.run(command, cwd=ROOT)
        if int(completed.returncode) != 0:
            print(
                f"[LIFECYCLE] Active Recommendations returned {completed.returncode}; Final Watchlist tetap sukses.",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"[LIFECYCLE] Active Recommendations launcher failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def _run_watchlist_ai(forwarded: list[str], trade_date: str) -> None:
    """Run the optional AI lane after official Final Watchlist success.

    This function deliberately swallows every AI-side failure. The child Final
    Watchlist return code has already been established and remains authoritative.
    """
    command = [
        sys.executable,
        "-u",
        str(ROOT / "tools/run_watchlist_ai.py"),
        "--trade-date",
        trade_date,
    ]
    for option in ("--config", "--scheduler-config"):
        value = _arg_value(forwarded, option)
        if value:
            command.extend([option, value])
    for flag in ("--dry-run", "--no-telegram", "--force"):
        if flag in forwarded:
            command.append(flag)
    try:
        completed = subprocess.run(command, cwd=ROOT)
        if int(completed.returncode) != 0:
            print(
                f"[WATCHLIST AI] Non-blocking runner returned {completed.returncode}; Final Watchlist tetap sukses.",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"[WATCHLIST AI] Non-blocking launcher failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if argv is None else argv)
    started_at = datetime.now(ZoneInfo("Asia/Jakarta"))
    previous_status = read_json(STATUS_PATH)

    try:
        trade_date = resolve_effective_trade_date(forwarded)
    except Exception as exc:
        trade_date = datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat()
        print(f"[FINAL WATCHLIST] Gagal menentukan effective trading date: {exc}", file=sys.stderr)
        write_orchestration_failure(
            trade_date=trade_date,
            exit_code=1,
            started_at=started_at,
            previous_status=previous_status,
            observed_status=read_json(STATUS_PATH),
        )
        return 1

    if not _arg_value(forwarded, "--trade-date"):
        forwarded.extend(["--trade-date", trade_date])

    print(f"[FINAL WATCHLIST] Effective trading date: {trade_date}", flush=True)
    command = [
        sys.executable,
        "-u",
        str(ROOT / "tools/run_final_watchlist_playwright_bridge.py"),
        *forwarded,
    ]
    completed = subprocess.run(command, cwd=ROOT)
    rc = int(completed.returncode)

    if rc != 0:
        write_orchestration_failure(
            trade_date=trade_date,
            exit_code=rc,
            started_at=started_at,
            previous_status=previous_status,
            observed_status=read_json(STATUS_PATH),
        )
        return rc

    # Official Final Watchlist is already complete. Both downstream steps are
    # isolated presentation/AI lanes and cannot change the authoritative rc.
    _run_active_recommendations(forwarded)
    _run_watchlist_ai(forwarded, trade_date)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
