from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import run_sde_job
import run_sde_job_integrated
from modules.job_runner.runtime import RunnerContext


def _ctx(tmp_path: Path, *, job: str, run_id: str) -> RunnerContext:
    return RunnerContext(
        job=job,
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 13),
        run_id=run_id,
        no_telegram=True,
        debug=True,
        config={"paths": {"manifest_dir": str(tmp_path / "manifests")}},
        scheduler_config={
            "paths": {
                "job_status_root": str(tmp_path / "custom-status"),
                "state_root": str(tmp_path / "state"),
                "job_log": str(tmp_path / "job.log"),
            },
            "runtime": {"global_resource_lock_enabled": False},
            "locks": {"stale_after_minutes": 60},
        },
        calendar_config={"holidays": [], "special_trading_days": []},
        config_provenance={"config_version": "test", "config_hash": "test"},
    )


def _standard_args(job: str) -> argparse.Namespace:
    return argparse.Namespace(
        job=job,
        config="config/pipeline.json",
        scheduler_config="config/scheduler.json",
        dry_run=False,
        preview_existing=False,
        interactive_broker=False,
        no_telegram=True,
        force=False,
        trade_date="2026-08-13",
        debug=True,
        engine_only=False,
        run_id="",
        reuse_yahoo_refresh=False,
        parent_managed_lifecycle=False,
    )


def _integrated_args(job: str) -> argparse.Namespace:
    return argparse.Namespace(
        job=job,
        config="config/pipeline.json",
        scheduler_config="config/scheduler.json",
        trade_date="2026-08-13",
        dry_run=False,
        preview_existing=False,
        interactive_broker=False,
        no_telegram=True,
        force=False,
        debug=True,
        reuse_yahoo_refresh=False,
    )


def _latest_traceback(ctx: RunnerContext) -> str:
    payload = json.loads(
        (ctx.status_root / f"{ctx.job}_latest.json").read_text(encoding="utf-8")
    )
    return str(payload["traceback_path"])


def test_standard_runner_traceback_uses_context_status_root(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx(tmp_path, job="full_manual", run_id="TRACE-STANDARD")
    monkeypatch.setattr(run_sde_job, "parse_args", lambda: _standard_args(ctx.job))
    monkeypatch.setattr(run_sde_job, "load_context", lambda **kwargs: ctx)
    monkeypatch.setattr(run_sde_job, "write_runtime_config_audit", lambda *args, **kwargs: None)
    monkeypatch.setitem(
        run_sde_job.JOBS,
        ctx.job,
        lambda current: (_ for _ in ()).throw(RuntimeError("standard failure")),
    )

    assert run_sde_job.main() == 1
    expected = ctx.status_root / "tracebacks" / "TRACE-STANDARD.txt"
    assert Path(_latest_traceback(ctx)) == expected
    assert "standard failure" in expected.read_text(encoding="utf-8")


def test_lock_boundary_traceback_uses_context_status_root(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx(tmp_path, job="full_manual", run_id="TRACE-LOCK")

    class BrokenLock:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("lock boundary failure")

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(run_sde_job, "parse_args", lambda: _standard_args(ctx.job))
    monkeypatch.setattr(run_sde_job, "load_context", lambda **kwargs: ctx)
    monkeypatch.setattr(run_sde_job, "write_runtime_config_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_sde_job, "FileLock", BrokenLock)

    assert run_sde_job.main() == 1
    expected = ctx.status_root / "tracebacks" / "TRACE-LOCK.txt"
    assert Path(_latest_traceback(ctx)) == expected
    assert "lock boundary failure" in expected.read_text(encoding="utf-8")


def test_integrated_runner_traceback_uses_context_status_root(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx(tmp_path, job="job_status", run_id="TRACE-INTEGRATED")
    monkeypatch.setattr(
        run_sde_job_integrated,
        "parse_args",
        lambda: _integrated_args(ctx.job),
    )
    monkeypatch.setattr(run_sde_job_integrated, "load_context", lambda **kwargs: ctx)
    monkeypatch.setattr(
        run_sde_job_integrated,
        "_run_integrated",
        lambda args, current: (_ for _ in ()).throw(RuntimeError("integrated failure")),
    )

    assert run_sde_job_integrated.main() == 1
    expected = ctx.status_root / "tracebacks" / "TRACE-INTEGRATED-integrated.txt"
    assert Path(_latest_traceback(ctx)) == expected
    assert "integrated failure" in expected.read_text(encoding="utf-8")
