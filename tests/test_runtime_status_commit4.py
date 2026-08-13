from __future__ import annotations

import json
import socket
from datetime import date
from pathlib import Path

import pytest

import modules.job_runner.runtime as runtime
from modules.job_runner.runtime import (
    EXIT_INTERRUPTED,
    EXIT_SUCCESS,
    FileLock,
    JobAlreadyRunning,
    RunnerContext,
    write_status,
)


def _ctx(tmp_path: Path, *, run_id: str = "COMMIT4-TEST", job: str = "post_market") -> RunnerContext:
    return RunnerContext(
        job=job,
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 13),
        run_id=run_id,
        config={},
        scheduler_config={
            "paths": {
                "preview_root": str(tmp_path / "previews"),
                "job_status_root": str(tmp_path / "status"),
                "state_root": str(tmp_path / "state"),
                "job_log": str(tmp_path / "job.log"),
            },
            "locks": {"stale_after_minutes": 60},
        },
        calendar_config={},
        config_provenance={},
    )


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_resend_status_never_overwrites_engine_latest(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, run_id="RESEND-1")
    engine_latest = ctx.status_root / "post_market_latest.json"
    engine_latest.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "run_id": "ENGINE-ORIGINAL",
        "status": "SUCCESS",
        "status_v1_7": "SUCCESS",
        "engine_status": "SUCCESS",
        "content_hash": "engine-hash",
    }
    engine_latest.write_text(json.dumps(original, indent=2), encoding="utf-8")

    write_status(
        ctx,
        "RUNNING",
        "RESEND_EXISTING",
        EXIT_SUCCESS,
        {"engine_status": "NOT_RUN", "delivery_status": "NOT_RUN"},
    )
    write_status(
        ctx,
        "SUCCESS",
        "POST_MARKET_RESEND",
        EXIT_SUCCESS,
        {
            "engine_status": "NOT_RUN",
            "delivery_status": "SENT",
            "telegram_status": "SENT",
            "source_run_id": "ENGINE-ORIGINAL",
            "warnings": ["RESEND_EXISTING_ARTIFACT; engine tidak dijalankan ulang."],
        },
    )

    assert _read(engine_latest) == original
    delivery = _read(ctx.status_root / "post_market_delivery_latest.json")
    assert delivery["status_channel"] == "DELIVERY"
    assert delivery["operation"] == "RESEND"
    assert delivery["source_engine_run_id"] == "ENGINE-ORIGINAL"
    assert delivery["engine_mutation"] == "NONE"


def test_normal_delivery_has_separate_delivery_channel(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    write_status(
        ctx,
        "SUCCESS",
        "POST_MARKET",
        EXIT_SUCCESS,
        {
            "delivery": [{"status": "SENT", "telegram_message_id": 10}],
            "telegram_status": "SENT",
        },
    )
    engine = _read(ctx.status_root / "post_market_latest.json")
    delivery = _read(ctx.status_root / "post_market_delivery_latest.json")
    assert engine["status_channel"] == "ENGINE"
    assert engine["engine_status"] == "SUCCESS"
    assert engine["delivery_status"] == "SENT"
    assert delivery["status_channel"] == "DELIVERY"
    assert delivery["operation"] == "DELIVERY"


def test_failed_terminal_cannot_publish_zero_exit_code(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    write_status(ctx, "FAILED", "BROKEN_STAGE", 0, {"errors": ["boom"]})
    payload = _read(ctx.status_root / "post_market_latest.json")
    assert payload["status_v1_7"] == "FAILED"
    assert payload["exit_code"] == 1
    assert "EXIT_CODE_NORMALIZED_FROM_ZERO_FOR_FAILED_STATUS" in payload["warnings"]


def test_keyboard_interrupt_terminalizes_before_job_lock_release(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, run_id="INTERRUPTED")
    lock_path = ctx.state_root / "locks" / "post_market.lock"

    with pytest.raises(KeyboardInterrupt):
        with FileLock(ctx):
            raise KeyboardInterrupt()

    payload = _read(ctx.status_root / "post_market_latest.json")
    assert payload["status_v1_7"] == "FAILED"
    assert payload["current_stage"] == "INTERRUPTED"
    assert payload["exit_code"] == EXIT_INTERRUPTED
    assert payload["finished_at"]
    assert payload["traceback_path"]
    assert Path(payload["traceback_path"]).exists()
    assert not lock_path.exists()

    events = [
        json.loads(line)["event"]
        for line in ctx.log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events.index("STATUS_FAILED") < events.index("JOB_LOCK_RELEASED")
    assert "INTERRUPT_TERMINALIZED" in events
    assert "LOCK_RELEASE_WITHOUT_TERMINAL_STATUS" not in events


def test_recent_foreign_host_lock_is_not_removed_by_local_pid_probe(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    lock_path = ctx.state_root / "locks" / "post_market.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "run_id": "FOREIGN",
                "pid": 99999999,
                "host": "other-host.example",
                "created_at": runtime.now_wib().isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(JobAlreadyRunning):
        with FileLock(ctx):
            pass
    assert lock_path.exists()


def test_dead_local_owner_lock_is_removed_safely(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    lock_path = ctx.state_root / "locks" / "post_market.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "run_id": "DEAD-LOCAL",
                "pid": 42424242,
                "host": socket.gethostname(),
                "created_at": runtime.now_wib().isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime._baseline, "is_process_alive", lambda pid: False)

    with FileLock(ctx):
        write_status(ctx, "SUCCESS", "TEST", EXIT_SUCCESS, {})

    assert not lock_path.exists()
    events = [
        json.loads(line)["event"]
        for line in ctx.log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "STALE_JOB_LOCK_REMOVED" in events


def test_lock_release_never_unlinks_replacement_owner(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    lock = FileLock(ctx)
    with lock:
        replacement = {
            "lock_token": "replacement-token",
            "run_id": "SUCCESSOR",
            "job": ctx.job,
            "kind": "job",
            "pid": 999,
            "host": "successor-host",
            "created_at": runtime.now_wib().isoformat(timespec="seconds"),
        }
        lock.path.write_text(json.dumps(replacement), encoding="utf-8")
        write_status(ctx, "SUCCESS", "TEST", EXIT_SUCCESS, {})

    assert lock.path.exists()
    assert _read(lock.path)["run_id"] == "SUCCESSOR"
    lock.path.unlink()
