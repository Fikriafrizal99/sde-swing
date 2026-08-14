from __future__ import annotations

"""Commit 4 runtime/status facade."""

import hashlib
import json
import os
import socket
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from modules.job_runner import runtime_baseline as _baseline
from swing_utils import atomic_write_text as _durable_atomic_write_text

for _name in dir(_baseline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_baseline, _name)

EXIT_INTERRUPTED = 130
RUNTIME_STATUS_CONTRACT_VERSION = "SDE_RUNTIME_STATUS_V1"
_CURRENT_HOST = socket.gethostname()


def _atomic_status_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{token}.tmp")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


_baseline.write_json = _atomic_status_json
_baseline_write_status = _baseline.write_status


def _terminal_exit_code(status: str, exit_code: int) -> tuple[int, list[str]]:
    warnings: list[str] = []
    normalized = _baseline._normalized_runtime_status(status)
    code = int(exit_code)
    if str(status).upper() != "RUNNING" and normalized == "FAILED" and code == 0:
        code = _baseline.EXIT_FAILED
        warnings.append("EXIT_CODE_NORMALIZED_FROM_ZERO_FOR_FAILED_STATUS")
    return code, warnings


def _is_resend_operation(ctx: RunnerContext, stage: str, details: dict[str, Any]) -> bool:
    upper = str(stage or "").upper()
    if bool(getattr(ctx, "delivery_only", False)) or "RESEND" in upper:
        return True
    return (
        str(details.get("engine_status", "")).upper() == "NOT_RUN"
        and any("RESEND" in str(item).upper() for item in details.get("warnings", []) or [])
    )


def _delivery_value(details: dict[str, Any], fallback_status: str) -> str:
    explicit = str(details.get("delivery_status") or details.get("telegram_status") or "").strip().upper()
    if explicit:
        return explicit
    rows = details.get("delivery")
    if isinstance(rows, list):
        statuses = [str(row.get("status", "")).upper() for row in rows if isinstance(row, dict)]
        if any(value == "FAILED" for value in statuses):
            return "FAILED"
        if any(value.startswith("SENT") for value in statuses):
            return "SENT"
        if statuses and all(value == "DUPLICATE_SUPPRESSED" for value in statuses):
            return "DUPLICATE_SUPPRESSED"
        if statuses:
            return "SKIPPED"
    return str(fallback_status or "NOT_RUN").upper()


def _engine_value(status: str, details: dict[str, Any]) -> str:
    explicit = str(details.get("engine_status") or "").strip().upper()
    if explicit and explicit != "NOT_RUN":
        return _baseline._normalized_runtime_status(explicit)
    normalized = _baseline._normalized_runtime_status(status)
    rows = details.get("delivery")
    if isinstance(rows, list):
        statuses = [str(row.get("status", "")).upper() for row in rows if isinstance(row, dict)]
        if str(status).upper() == "DELIVERY_FAILED":
            return "SUCCESS_WITH_WARNING" if (details.get("warnings") or []) else "SUCCESS"
        if statuses and all(value in {"DUPLICATE_SUPPRESSED", "SKIPPED_NOT_CONFIGURED"} for value in statuses):
            return "SUCCESS_WITH_WARNING" if (details.get("warnings") or []) else "SUCCESS"
    return normalized if str(status).upper() != "RUNNING" else "RUNNING"


def _read_engine_snapshot(ctx: RunnerContext) -> dict[str, Any]:
    payload = _baseline.read_json(ctx.status_root / f"{ctx.job}_latest.json")
    return payload if isinstance(payload, dict) else {}


def write_delivery_status(
    ctx: RunnerContext,
    status: str,
    stage: str,
    exit_code: int,
    details: dict[str, Any] | None = None,
    *,
    operation: str = "DELIVERY",
    mark_context_terminal: bool = True,
) -> Path:
    detail_payload = dict(details or {})
    code, consistency_warnings = _terminal_exit_code(status, exit_code)
    if consistency_warnings:
        detail_payload["warnings"] = [*(detail_payload.get("warnings", []) or []), *consistency_warnings]

    now = _baseline.now_wib()
    final_status = str(status).upper() != "RUNNING"
    engine_snapshot = _read_engine_snapshot(ctx)
    normalized_operation = str(operation or "DELIVERY").upper()
    payload: dict[str, Any] = {
        "run_id": ctx.run_id,
        "job": ctx.job,
        "status_channel": "DELIVERY",
        "operation": normalized_operation,
        "runtime_status_contract": RUNTIME_STATUS_CONTRACT_VERSION,
        "status": _baseline._normalized_runtime_status(status) if final_status else "RUNNING",
        "legacy_status": str(status),
        "delivery_status": _delivery_value(detail_payload, status),
        "current_stage": stage,
        "exit_code": code,
        "trade_date": ctx.trade_date.isoformat(),
        "started_at": ctx.started_at.isoformat(timespec="seconds"),
        "finished_at": now.isoformat(timespec="seconds") if final_status else "",
        "updated_at": now.isoformat(timespec="seconds"),
        "source_engine_run_id": detail_payload.get("source_run_id") or engine_snapshot.get("run_id", ""),
        "source_engine_status": engine_snapshot.get("engine_status") or engine_snapshot.get("status_v1_7") or engine_snapshot.get("status", ""),
        "source_engine_content_hash": engine_snapshot.get("content_hash", ""),
        "engine_mutation": "NONE",
        "telegram_status": detail_payload.get("telegram_status", ""),
        "telegram_message_ids": detail_payload.get("telegram_message_ids", []),
        "telegram_part_count": detail_payload.get("telegram_part_count", 0),
        "warnings": detail_payload.get("warnings", []),
        "errors": detail_payload.get("errors", []),
        "traceback_path": detail_payload.get("traceback_path", ""),
        "hostname": _CURRENT_HOST,
        "process_id": os.getpid(),
        "details": detail_payload,
    }
    payload["content_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()

    root = ctx.status_root / "delivery"
    dated = root / ctx.trade_date.isoformat()
    slug = normalized_operation.lower().replace(" ", "_")
    target = dated / f"{ctx.job}_{ctx.run_id}_{slug}.json"
    latest = ctx.status_root / f"{ctx.job}_delivery_latest.json"
    _atomic_status_json(target, payload)
    _atomic_status_json(latest, payload)

    event_status = payload["status"] if final_status else "RUNNING"
    _baseline.append_job_log(ctx, f"{normalized_operation}_STATUS_{event_status}", stage)
    if final_status:
        if mark_context_terminal:
            setattr(ctx, "_terminal_status_written", True)
            setattr(ctx, "_terminal_status_channel", "DELIVERY")
        _baseline.append_job_log(ctx, f"{normalized_operation}_FINAL_EXIT_CODE", str(code))
    return target


def write_status(
    ctx: RunnerContext,
    status: str,
    stage: str,
    exit_code: int,
    details: dict[str, Any] | None = None,
) -> Path:
    detail_payload = dict(details or {})
    if _is_resend_operation(ctx, stage, detail_payload):
        return write_delivery_status(ctx, status, stage, exit_code, detail_payload, operation="RESEND")

    code, consistency_warnings = _terminal_exit_code(status, exit_code)
    if consistency_warnings:
        detail_payload["warnings"] = [*(detail_payload.get("warnings", []) or []), *consistency_warnings]

    target = _baseline_write_status(ctx, status, stage, code, detail_payload)
    payload = _baseline.read_json(target)
    if not isinstance(payload, dict):
        payload = {}

    payload["status_channel"] = "ENGINE"
    payload["runtime_status_contract"] = RUNTIME_STATUS_CONTRACT_VERSION
    payload["engine_status"] = _engine_value(status, detail_payload)
    payload["delivery_status"] = _delivery_value(detail_payload, "NOT_RUN")
    payload["process_status"] = payload.get("status_v1_7") or payload.get("status")
    payload["content_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in payload.items() if k != "content_hash"}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()

    latest = ctx.status_root / f"{ctx.job}_latest.json"
    _atomic_status_json(target, payload)
    _atomic_status_json(latest, payload)

    if isinstance(detail_payload.get("delivery"), list) or str(detail_payload.get("delivery_status", "")).strip():
        delivery_value = _delivery_value(detail_payload, status)
        delivery_failed = delivery_value == "FAILED"
        delivery_skipped = delivery_value.startswith("SKIP") or delivery_value in {"NOT_RUN", "NOT_CONFIGURED"}
        write_delivery_status(
            ctx,
            "FAILED" if delivery_failed else ("SKIPPED" if delivery_skipped else "SUCCESS"),
            stage,
            _baseline.EXIT_DELIVERY_FAILED if delivery_failed else _baseline.EXIT_SUCCESS,
            detail_payload,
            operation="DELIVERY",
            mark_context_terminal=False,
        )
        if str(status).upper() != "RUNNING":
            setattr(ctx, "_terminal_status_written", True)
            setattr(ctx, "_terminal_status_channel", "ENGINE")
    return target


def write_traceback(ctx: RunnerContext, suffix: str = "", rendered: str | None = None) -> str:
    """Persist a traceback below the runtime context's configured status root."""

    trace_dir = ctx.status_root / "tracebacks"
    trace_dir.mkdir(parents=True, exist_ok=True)
    normalized_suffix = str(suffix or "").strip().strip("-")
    filename = f"{ctx.run_id}-{normalized_suffix}.txt" if normalized_suffix else f"{ctx.run_id}.txt"
    path = trace_dir / filename
    body = rendered if rendered is not None else traceback.format_exc()
    _durable_atomic_write_text(path, body or "TRACEBACK_UNAVAILABLE")
    return str(path)


def _traceback_for_lock_exit(ctx: RunnerContext, exc_type, exc, tb) -> str:
    suffix = "interrupt" if exc_type and issubclass(exc_type, KeyboardInterrupt) else "unhandled"
    rendered = "".join(traceback.format_exception(exc_type, exc, tb)) if exc_type else ""
    return write_traceback(ctx, suffix, rendered or suffix.upper())


class FileLock:
    def __init__(self, ctx: RunnerContext, name: str | None = None, kind: str = "job"):
        self.ctx = ctx
        lock_cfg = ctx.scheduler_config.get("locks", {})
        self.stale_after = int(lock_cfg.get("stale_after_minutes", 180))
        self.kind = kind
        filename = name or f"{ctx.job}.lock"
        self.path = ctx.state_root / "locks" / filename
        self._fd: int | None = None
        self._token = uuid.uuid4().hex
        self._created_here = False

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clear_stale_if_needed()
        payload = {
            "lock_token": self._token,
            "run_id": self.ctx.run_id,
            "job": self.ctx.job,
            "kind": self.kind,
            "pid": os.getpid(),
            "host": _CURRENT_HOST,
            "created_at": _baseline.now_wib().isoformat(timespec="seconds"),
            "trade_date": self.ctx.trade_date.isoformat(),
        }
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            self._created_here = True
            os.write(self._fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            os.fsync(self._fd)
            _baseline.append_job_log(self.ctx, f"{self.kind.upper()}_LOCK_ACQUIRED", str(self.path))
            return self
        except FileExistsError as exc:
            if self.kind == "global_resource":
                raise _baseline.ResourceLocked(f"Resource lock masih aktif: {self.path}") from exc
            raise _baseline.JobAlreadyRunning(f"Lock masih aktif: {self.path}") from exc
        except BaseException:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            if self._created_here:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.kind == "job" and not bool(getattr(self.ctx, "_terminal_status_written", False)):
                if exc_type is not None:
                    trace_path = _traceback_for_lock_exit(self.ctx, exc_type, exc, tb)
                    interrupted = issubclass(exc_type, KeyboardInterrupt)
                    stage = "INTERRUPTED" if interrupted else "UNHANDLED_EXCEPTION_AT_LOCK_RELEASE"
                    code = EXIT_INTERRUPTED if interrupted else _baseline.EXIT_FAILED
                    label = f"{getattr(exc_type, '__name__', 'BaseException')}: {exc or ''}".strip()
                    write_status(
                        self.ctx,
                        "FAILED",
                        stage,
                        code,
                        {"error": label, "errors": [label], "traceback_path": trace_path, "lock_status": "RELEASING_AFTER_EXCEPTION"},
                    )
                    _baseline.append_job_log(
                        self.ctx,
                        "INTERRUPT_TERMINALIZED" if interrupted else "UNHANDLED_EXCEPTION_TERMINALIZED",
                        stage,
                    )
        except Exception as err:
            _baseline.append_job_log(self.ctx, f"{self.kind.upper()}_TERMINALIZE_WARNING", str(err))
        finally:
            if self.kind == "job" and not bool(getattr(self.ctx, "_terminal_status_written", False)):
                _baseline.append_job_log(
                    self.ctx,
                    "LOCK_RELEASE_WITHOUT_TERMINAL_STATUS",
                    f"exception={getattr(exc_type, '__name__', '')}",
                )
            try:
                if self._fd is not None:
                    os.close(self._fd)
                    self._fd = None
                if self._unlink_if_owned():
                    _baseline.append_job_log(self.ctx, f"{self.kind.upper()}_LOCK_RELEASED", str(self.path))
                elif self.path.exists():
                    _baseline.append_job_log(self.ctx, f"{self.kind.upper()}_LOCK_RELEASE_OWNERSHIP_MISMATCH", str(self.path))
            except Exception as err:
                _baseline.append_job_log(self.ctx, f"{self.kind.upper()}_LOCK_RELEASE_WARNING", str(err))

    def _snapshot(self) -> tuple[str, dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except Exception:
            return "", {}
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        return raw, payload if isinstance(payload, dict) else {}

    def _owns_current_lock(self) -> bool:
        if not self.path.exists():
            return False
        _, payload = self._snapshot()
        try:
            pid = int(payload.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        return (
            str(payload.get("lock_token") or "") == self._token
            and str(payload.get("run_id") or "") == str(self.ctx.run_id)
            and pid == os.getpid()
            and str(payload.get("host") or "") == _CURRENT_HOST
        )

    def _unlink_if_owned(self) -> bool:
        if not self._owns_current_lock():
            return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True

    def _clear_stale_if_needed(self) -> None:
        if not self.path.exists():
            return
        raw, payload = self._snapshot()
        created_raw = str(payload.get("created_at", ""))
        try:
            pid = int(payload.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        host = str(payload.get("host") or "")
        try:
            created = datetime.fromisoformat(created_raw)
            if created.tzinfo is None:
                created = created.replace(tzinfo=_baseline.WIB)
        except Exception:
            created = _baseline.now_wib() - timedelta(minutes=self.stale_after + 1)
        age = _baseline.now_wib() - created.astimezone(_baseline.WIB)
        stale_by_age = age > timedelta(minutes=self.stale_after)
        stale_by_local_process = bool(host) and host == _CURRENT_HOST and not _baseline.is_process_alive(pid)
        if not (stale_by_age or stale_by_local_process):
            return
        try:
            current_raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        if current_raw != raw:
            _baseline.append_job_log(self.ctx, f"STALE_{self.kind.upper()}_LOCK_CHANGED_SKIP", str(self.path))
            return
        try:
            self.path.unlink()
            reason = "AGE" if stale_by_age else "LOCAL_OWNER_DEAD"
            _baseline.append_job_log(self.ctx, f"STALE_{self.kind.upper()}_LOCK_REMOVED", f"{self.path}|reason={reason}")
        except FileNotFoundError:
            return


_baseline.FileLock = FileLock
_baseline.write_status = write_status
