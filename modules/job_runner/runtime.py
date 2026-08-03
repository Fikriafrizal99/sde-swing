from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from swing_utils import make_run_id
from modules.runtime_config import load_runtime_config


ROOT = Path(__file__).resolve().parents[2]
WIB = ZoneInfo("Asia/Jakarta")

EXIT_SUCCESS = 0
EXIT_FAILED = 1
EXIT_SKIPPED = 10
EXIT_WAITING_DATA = 20
EXIT_DUPLICATE = 30
EXIT_RESOURCE_LOCKED = 40
EXIT_DELIVERY_FAILED = 50


class JobAlreadyRunning(RuntimeError):
    def __init__(self, message: str, status: str = "SKIPPED_ALREADY_RUNNING"):
        super().__init__(message)
        self.status = status


class ResourceLocked(JobAlreadyRunning):
    def __init__(self, message: str):
        super().__init__(message, status="RESOURCE_LOCKED")


def resolve(value: str | Path) -> Path:
    path = Path(os.path.expandvars(str(value)))
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def now_wib() -> datetime:
    return datetime.now(WIB)


def parse_trade_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return now_wib().date()


def parse_hhmm(value: str, fallback: str) -> time:
    text = value or fallback
    hour, minute = text.split(":", 1)
    return time(int(hour), int(minute), tzinfo=WIB)


def make_job_run_id(job: str) -> str:
    prefix = "SDE-" + job.upper().replace("_", "-")
    return make_run_id(prefix=prefix)


@dataclass
class RunnerContext:
    job: str
    config_path: Path
    scheduler_config_path: Path
    trade_date: date
    run_id: str
    dry_run: bool = False
    preview_existing: bool = False
    interactive_broker: bool = False
    no_telegram: bool = False
    force: bool = False
    debug: bool = False
    started_at: datetime = field(default_factory=now_wib)
    config: dict[str, Any] = field(default_factory=dict)
    scheduler_config: dict[str, Any] = field(default_factory=dict)
    calendar_config: dict[str, Any] = field(default_factory=dict)
    config_provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def previews_root(self) -> Path:
        value = self.scheduler_config.get("paths", {}).get("preview_root", "data/output/previews")
        return resolve(value)

    @property
    def status_root(self) -> Path:
        value = self.scheduler_config.get("paths", {}).get("job_status_root", "data/output/job_status")
        return resolve(value)

    @property
    def state_root(self) -> Path:
        value = self.scheduler_config.get("paths", {}).get("state_root", "data/state/scheduler")
        return resolve(value)

    @property
    def log_path(self) -> Path:
        value = self.scheduler_config.get("paths", {}).get("job_log", "logs/sde_job_runner.log")
        return resolve(value)

    def path(self, name: str, default: str = "") -> Path:
        value = self.config.get("paths", {}).get(name, default)
        return resolve(value)

    @property
    def mode(self) -> str:
        if self.preview_existing:
            return "PREVIEW_EXISTING"
        if self.dry_run:
            return "DRY_RUN"
        return "LIVE"

    def scheduled_time(self) -> str:
        if self.job == "final_watchlist":
            return str(self.scheduler_config.get("final_watchlist", {}).get("start_time", "18:00"))
        return str(self.scheduler_config.get(self.job, {}).get("time", "") or self.scheduler_config.get("jobs", {}).get(self.job, {}).get("time_wib", ""))


def load_context(
    job: str,
    config_path: str,
    scheduler_config_path: str,
    trade_date: str | None,
    dry_run: bool,
    preview_existing: bool,
    no_telegram: bool,
    force: bool,
    debug: bool,
    interactive_broker: bool = False,
) -> RunnerContext:
    cfg_path = resolve(config_path)
    sched_path = resolve(scheduler_config_path)
    scheduler_cfg = read_json(sched_path)
    strict_config = cfg_path.name.lower() == "pipeline.json"
    pipeline_cfg, config_provenance = load_runtime_config(cfg_path, strict=strict_config)
    calendar_path = resolve(scheduler_cfg.get("trading_calendar", "config/trading_calendar.json"))
    ctx = RunnerContext(
        job=job,
        config_path=cfg_path,
        scheduler_config_path=sched_path,
        trade_date=parse_trade_date(trade_date),
        run_id=make_job_run_id(job),
        dry_run=dry_run,
        preview_existing=preview_existing,
        interactive_broker=interactive_broker,
        no_telegram=no_telegram,
        force=force,
        debug=debug,
        config=pipeline_cfg,
        scheduler_config=scheduler_cfg,
        calendar_config=read_json(calendar_path),
        config_provenance=config_provenance,
    )
    return ctx


def append_job_log(ctx: RunnerContext, event: str, detail: str = "") -> None:
    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "time": now_wib().isoformat(timespec="seconds"),
        "run_id": ctx.run_id,
        "job": ctx.job,
        "event": event,
        "detail": detail,
    }
    with ctx.log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_status(
    ctx: RunnerContext,
    status: str,
    stage: str,
    exit_code: int,
    details: dict[str, Any] | None = None,
) -> Path:
    finished_at = now_wib()
    final_status = status != "RUNNING"
    duration = (finished_at - ctx.started_at).total_seconds() if final_status else None
    detail_payload = details or {}
    payload = {
        "run_id": ctx.run_id,
        "job": ctx.job,
        "job_name": ctx.job,
        "job_mode": ctx.mode,
        "status": status,
        "current_stage": stage,
        "exit_code": exit_code,
        "trade_date": ctx.trade_date.isoformat(),
        "scheduled_time": ctx.scheduled_time(),
        "timezone": "Asia/Jakarta",
        "dry_run": ctx.dry_run,
        "preview_existing": ctx.preview_existing,
        "no_telegram": ctx.no_telegram,
        "force": ctx.force,
        "started_at": ctx.started_at.isoformat(timespec="seconds"),
        "actual_start_time": ctx.started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds") if final_status else "",
        "duration_seconds": duration,
        "updated_at": now_wib().isoformat(timespec="seconds"),
        "data_status": detail_payload.get("data_status", detail_payload.get("Data_Quality_Status", "")),
        "provider_status": detail_payload.get("provider_status", ""),
        "snapshot_id": detail_payload.get("snapshot_id", ""),
        "snapshot_trade_date": detail_payload.get("snapshot_trade_date", ""),
        "global_market_snapshot_id": detail_payload.get("global_market_snapshot_id", ""),
        "global_market_coverage_ratio": detail_payload.get("global_market_coverage_ratio", ""),
        "global_sentiment_state": detail_payload.get("global_sentiment_state", ""),
        "global_sentiment_score": detail_payload.get("global_sentiment_score", ""),
        "dependency_run_id": detail_payload.get("dependency_run_id", ""),
        "dependency_status": detail_payload.get("dependency_status", ""),
        "broker_readiness_status": detail_payload.get("broker_readiness_status", detail_payload.get("reason", "")),
        "broker_summary_date": detail_payload.get("broker_date", ""),
        "retry_count": detail_payload.get("retry_count", detail_payload.get("attempts", 0)),
        "symbols_loaded": detail_payload.get("symbols_loaded", 0),
        "symbols_analyzed": detail_payload.get("symbols_analyzed", 0),
        "symbols_valid": detail_payload.get("symbols_valid", 0),
        "symbols_failed": detail_payload.get("symbols_failed", 0),
        "symbols_skipped": detail_payload.get("symbols_skipped", 0),
        "output_paths": detail_payload.get("output_paths", {}),
        "preview_paths": detail_payload.get("preview_paths", detail_payload.get("previews", [])),
        "telegram_status": detail_payload.get("telegram_status", ""),
        "telegram_message_ids": detail_payload.get("telegram_message_ids", []),
        "telegram_part_count": detail_payload.get("telegram_part_count", 0),
        "warnings": detail_payload.get("warnings", []),
        "errors": detail_payload.get("errors", []),
        "traceback_path": detail_payload.get("traceback_path", ""),
        "data_source_mode": detail_payload.get("data_source_mode", ""),
        "hostname": socket.gethostname(),
        "process_id": os.getpid(),
        "lock_status": detail_payload.get("lock_status", ""),
        "global_resource_lock_status": detail_payload.get("global_resource_lock_status", ""),
        "config_source": ctx.config_provenance.get("config_source", str(ctx.config_path)),
        "config_hash": ctx.config_provenance.get("config_hash", ""),
        "config_version": ctx.config_provenance.get("config_version", ""),
        "config_loaded_at": ctx.config_provenance.get("loaded_at", ""),
        "config_validation_status": ctx.config_provenance.get("validation_status", ""),
        "config_override_mode": ctx.config_provenance.get("override_mode", "NONE"),
        "details": detail_payload,
    }
    dated = ctx.status_root / ctx.trade_date.isoformat()
    target = dated / f"{ctx.job}_{ctx.run_id}.json"
    latest = ctx.status_root / f"{ctx.job}_latest.json"
    write_json(target, payload)
    write_json(latest, payload)
    append_job_log(ctx, f"STATUS_{status}", stage)
    return target


def trading_day_status(ctx: RunnerContext) -> tuple[bool, str]:
    calendar = ctx.calendar_config
    day = ctx.trade_date
    special = {str(x) for x in calendar.get("special_trading_days", [])}
    holidays = {str(x) for x in calendar.get("holidays", [])}
    if day.isoformat() in special:
        return True, "SPECIAL_TRADING_DAY"
    if day.isoformat() in holidays:
        return False, "SKIPPED_NON_TRADING_DAY"
    if day.weekday() >= 5:
        return False, "SKIPPED_NON_TRADING_DAY"
    return True, "TRADING_DAY"


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


class FileLock:
    def __init__(self, ctx: RunnerContext, name: str | None = None, kind: str = "job"):
        self.ctx = ctx
        lock_cfg = ctx.scheduler_config.get("locks", {})
        self.stale_after = int(lock_cfg.get("stale_after_minutes", 180))
        self.kind = kind
        filename = name or f"{ctx.job}.lock"
        self.path = ctx.state_root / "locks" / filename
        self._fd: int | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clear_stale_if_needed()
        payload = {
            "run_id": self.ctx.run_id,
            "job": self.ctx.job,
            "kind": self.kind,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at": now_wib().isoformat(timespec="seconds"),
            "trade_date": self.ctx.trade_date.isoformat(),
        }
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            append_job_log(self.ctx, f"{self.kind.upper()}_LOCK_ACQUIRED", str(self.path))
            return self
        except FileExistsError as exc:
            if self.kind == "global_resource":
                raise ResourceLocked(f"Resource lock masih aktif: {self.path}") from exc
            raise JobAlreadyRunning(f"Lock masih aktif: {self.path}") from exc

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
            if self.path.exists():
                self.path.unlink()
            append_job_log(self.ctx, f"{self.kind.upper()}_LOCK_RELEASED", str(self.path))
        except Exception as err:
            append_job_log(self.ctx, f"{self.kind.upper()}_LOCK_RELEASE_WARNING", str(err))

    def _clear_stale_if_needed(self) -> None:
        if not self.path.exists():
            return
        payload = read_json(self.path)
        created_raw = str(payload.get("created_at", ""))
        pid = int(payload.get("pid") or 0)
        try:
            created = datetime.fromisoformat(created_raw)
            if created.tzinfo is None:
                created = created.replace(tzinfo=WIB)
        except Exception:
            created = now_wib() - timedelta(minutes=self.stale_after + 1)
        age = now_wib() - created.astimezone(WIB)
        if age > timedelta(minutes=self.stale_after) or not is_process_alive(pid):
            self.path.unlink(missing_ok=True)
            append_job_log(self.ctx, f"STALE_{self.kind.upper()}_LOCK_REMOVED", str(self.path))


def latest_matching_file(folder: Path, pattern: str) -> Path | None:
    if not folder.exists():
        return None
    matches = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None
