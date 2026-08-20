#!/usr/bin/env python3
from __future__ import annotations

"""Reliable non-interactive wrapper for Windows-scheduled SDE jobs.

The wrapper keeps scheduler concerns out of the trading engines:
- calls the same canonical entrypoint used by the manual launcher;
- records scheduled-vs-actual start time;
- retries only configured transient failures;
- treats SKIPPED/DUPLICATE exit codes as successful scheduler completions.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
WIB = ZoneInfo("Asia/Jakarta")
DEFAULT_CONFIG = ROOT / "config" / "scheduler.json"

EXIT_LABELS = {
    0: "SUCCESS",
    1: "FAILED",
    10: "SKIPPED",
    20: "WAITING_DATA",
    30: "DUPLICATE",
    40: "RESOURCE_LOCKED",
    50: "DELIVERY_FAILED",
}

CANONICAL_COMMANDS = {
    "market_outlook": ("run_sde_job_integrated.py", "--job", "market_outlook"),
    "post_market": ("run_sde_job_integrated_market_first.py", "--job", "post_market"),
    "final_watchlist": ("tools/run_final_watchlist_entrypoint.py", "--period", "1D"),
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"SCHEDULER_CONFIG_INVALID:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"SCHEDULER_CONFIG_NOT_OBJECT:{path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def resolve(path_value: str | Path) -> Path:
    path = Path(os.path.expandvars(str(path_value)))
    return path if path.is_absolute() else ROOT / path


def scheduled_time(job: str, cfg: dict[str, Any]) -> str:
    if job == "final_watchlist":
        return str(cfg.get("final_watchlist", {}).get("start_time", "18:00"))
    return str(cfg.get(job, {}).get("time", "")).strip()


def start_lateness_minutes(job: str, cfg: dict[str, Any], now: datetime) -> float:
    raw = scheduled_time(job, cfg)
    if not raw:
        return 0.0
    try:
        hour, minute = [int(value) for value in raw.split(":", 1)]
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (now - scheduled).total_seconds() / 60.0)


def _runtime_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    value = cfg.get("scheduler_runtime", {})
    return value if isinstance(value, dict) else {}


def _job_cfg(cfg: dict[str, Any], job: str) -> dict[str, Any]:
    runtime = _runtime_cfg(cfg)
    jobs = runtime.get("jobs", {})
    if not isinstance(jobs, dict):
        return {}
    value = jobs.get(job, {})
    return value if isinstance(value, dict) else {}


def _retry_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    value = _runtime_cfg(cfg).get("retry", {})
    return value if isinstance(value, dict) else {}


def _int_set(values: Any, fallback: tuple[int, ...]) -> set[int]:
    if not isinstance(values, list):
        return set(fallback)
    result: set[int] = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result or set(fallback)


def max_attempts_for(cfg: dict[str, Any], job: str) -> int:
    retry = _retry_cfg(cfg)
    job_cfg = _job_cfg(cfg, job)
    raw = job_cfg.get("max_attempts", retry.get("max_attempts", 3))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 3


def generic_failure_max_attempts(cfg: dict[str, Any], job: str) -> int:
    retry = _retry_cfg(cfg)
    job_cfg = _job_cfg(cfg, job)
    raw = job_cfg.get(
        "generic_failure_max_attempts",
        retry.get("generic_failure_max_attempts", 2),
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


def retry_delay_seconds(cfg: dict[str, Any], attempt: int) -> int:
    values = _retry_cfg(cfg).get("backoff_seconds", [60, 180, 300])
    if not isinstance(values, list) or not values:
        values = [60, 180, 300]
    parsed: list[int] = []
    for value in values:
        try:
            parsed.append(max(0, int(value)))
        except (TypeError, ValueError):
            pass
    if not parsed:
        parsed = [60, 180, 300]
    return parsed[min(max(attempt - 1, 0), len(parsed) - 1)]


def classify_exit(
    cfg: dict[str, Any],
    job: str,
    code: int,
    attempt: int,
    *,
    lock_busy: bool = False,
) -> tuple[str, bool, int]:
    """Return (classification, should_retry, scheduler_exit_code)."""
    if code == 10 and lock_busy:
        return "JOB_LOCKED", attempt < max_attempts_for(cfg, job), code

    runtime = _runtime_cfg(cfg)
    success_codes = _int_set(
        runtime.get("success_equivalent_exit_codes"),
        (0, 10, 30),
    )
    if code in success_codes:
        return EXIT_LABELS.get(code, "SUCCESS_EQUIVALENT"), False, 0

    retry = _retry_cfg(cfg)
    transient = _int_set(
        retry.get("retryable_exit_codes"),
        (20, 40, 50),
    )
    generic = _int_set(
        retry.get("generic_failure_exit_codes"),
        (1,),
    )
    if code in transient:
        return EXIT_LABELS.get(code, "TRANSIENT_FAILURE"), attempt < max_attempts_for(cfg, job), code
    if code in generic:
        ceiling = min(max_attempts_for(cfg, job), generic_failure_max_attempts(cfg, job))
        return "GENERIC_FAILURE", attempt < ceiling, code
    return EXIT_LABELS.get(code, "NON_RETRYABLE_FAILURE"), False, code or 1


def latest_job_status(cfg: dict[str, Any], job: str) -> dict[str, Any]:
    paths = cfg.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}
    root = resolve(paths.get("job_status_root", "data/output/job_status"))
    path = root / f"{job}_latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def status_reports_lock_busy(payload: dict[str, Any]) -> bool:
    stage = str(payload.get("current_stage") or payload.get("stage") or "").upper()
    details = payload.get("details", {})
    if not isinstance(details, dict):
        details = {}
    lock_status = str(
        payload.get("lock_status") or details.get("lock_status") or ""
    ).upper()
    return stage == "LOCK" and lock_status == "BUSY"


def canonical_command(job: str, forwarded: list[str]) -> list[str]:
    spec = CANONICAL_COMMANDS.get(job)
    if spec is None:
        raise RuntimeError(f"UNSUPPORTED_SCHEDULED_JOB:{job}")
    return [sys.executable, "-u", str(ROOT / spec[0]), *spec[1:], *forwarded]


def _state_paths(cfg: dict[str, Any], job: str) -> tuple[Path, Path]:
    runtime = _runtime_cfg(cfg)
    state_root = resolve(runtime.get("state_root", "data/state/scheduler/scheduled_runs"))
    log_path = resolve(runtime.get("log_path", "logs/sde_scheduler.log"))
    return state_root / f"{job}_latest.json", log_path


def _record(
    latest_path: Path,
    log_path: Path,
    payload: dict[str, Any],
) -> None:
    atomic_write_json(latest_path, payload)
    append_jsonl(log_path, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reliable SDE Windows scheduler wrapper")
    parser.add_argument("--job", required=True, choices=tuple(CANONICAL_COMMANDS))
    parser.add_argument("--scheduler-config", default=str(DEFAULT_CONFIG))
    args, forwarded = parser.parse_known_args(argv)

    cfg_path = resolve(args.scheduler_config)
    try:
        cfg = read_json(cfg_path)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1

    now = datetime.now(WIB)
    lateness = start_lateness_minutes(args.job, cfg, now)
    runtime = _runtime_cfg(cfg)
    try:
        late_threshold = float(runtime.get("late_start_warning_minutes", 10))
    except (TypeError, ValueError):
        late_threshold = 10.0

    latest_path, log_path = _state_paths(cfg, args.job)
    max_attempts = max_attempts_for(cfg, args.job)
    command = canonical_command(args.job, forwarded)
    history: list[dict[str, Any]] = []
    started_at = now

    for attempt in range(1, max_attempts + 1):
        attempt_started = datetime.now(WIB)
        print(
            f"[SCHEDULER] {args.job} attempt {attempt}/{max_attempts} "
            f"| late={lateness:.1f}m",
            flush=True,
        )
        try:
            completed = subprocess.run(command, cwd=ROOT)
            child_code = int(completed.returncode)
        except KeyboardInterrupt:
            child_code = 130
        except Exception as exc:
            print(
                f"[SCHEDULER] launcher exception: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            child_code = 1

        status_snapshot = latest_job_status(cfg, args.job)
        classification, should_retry, scheduler_exit = classify_exit(
            cfg,
            args.job,
            child_code,
            attempt,
            lock_busy=status_reports_lock_busy(status_snapshot),
        )
        attempt_finished = datetime.now(WIB)
        row = {
            "attempt": attempt,
            "started_at": attempt_started.isoformat(timespec="seconds"),
            "finished_at": attempt_finished.isoformat(timespec="seconds"),
            "duration_seconds": round(
                (attempt_finished - attempt_started).total_seconds(), 1
            ),
            "child_exit_code": child_code,
            "classification": classification,
            "retry": bool(should_retry),
        }
        history.append(row)

        payload = {
            "job": args.job,
            "scheduled_time": scheduled_time(args.job, cfg),
            "actual_start_time": started_at.isoformat(timespec="seconds"),
            "start_lateness_minutes": round(lateness, 1),
            "late_start_warning": lateness > late_threshold,
            "attempt_count": attempt,
            "max_attempts": max_attempts,
            "child_exit_code": child_code,
            "scheduler_exit_code": 0 if scheduler_exit == 0 else scheduler_exit,
            "classification": classification,
            "status": "RETRYING" if should_retry else (
                "SUCCESS" if scheduler_exit == 0 else "FAILED"
            ),
            "attempts": history,
            "updated_at": attempt_finished.isoformat(timespec="seconds"),
            "command_entrypoint": str(command[2]) if len(command) > 2 else "",
        }
        _record(latest_path, log_path, payload)

        if not should_retry:
            if scheduler_exit == 0:
                print(
                    f"[SCHEDULER] {args.job} completed: {classification}",
                    flush=True,
                )
            else:
                print(
                    f"[SCHEDULER] {args.job} failed: {classification} "
                    f"(exit {child_code})",
                    file=sys.stderr,
                    flush=True,
                )
            return scheduler_exit

        delay = retry_delay_seconds(cfg, attempt)
        print(
            f"[SCHEDULER] transient failure {classification}; retry in {delay}s",
            flush=True,
        )
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 130

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
