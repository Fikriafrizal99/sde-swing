from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import pytest

import run_sde_job
from modules.data_sources.base import SourceTimeout, Transport, TransportResponse
from modules.data_sources.config import SourceConfig
from modules.data_sources.yahoo_zapi_validator import validate_yahoo_against_zapi
from modules.data_sources.zapi_idx_adapter import ZapiIdxClient
from modules.job_runner.core import SourceValidationBlocked
from modules.job_runner.runtime import RunnerContext, stage_watchdog


class TimeoutTransport(Transport):
    def request(self, method, path, *, params=None, headers=None, timeout=None):
        raise SourceTimeout("simulated timeout")


def _ctx(tmp_path: Path) -> RunnerContext:
    return RunnerContext(
        job="full_manual",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 4),
        run_id="TEST-FULL-MANUAL-TERMINAL",
        no_telegram=True,
        debug=True,
        config={"paths": {"manifest_dir": str(tmp_path / "manifests")}},
        scheduler_config={
            "paths": {
                "preview_root": str(tmp_path / "previews"),
                "job_status_root": str(tmp_path / "status"),
                "state_root": str(tmp_path / "state"),
                "job_log": str(tmp_path / "job.log"),
            },
            "runtime": {"global_resource_lock_enabled": True},
            "locks": {"global_resource_lock_name": "global.lock", "stale_after_minutes": 60},
        },
        calendar_config={"holidays": [], "special_trading_days": []},
        config_provenance={"config_version": "1.7.0-multisource", "config_hash": "test"},
    )


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        job="full_manual", config="config/pipeline.json", scheduler_config="config/scheduler.json",
        dry_run=False, preview_existing=False, interactive_broker=False, no_telegram=True,
        force=False, trade_date="2026-08-04", debug=True, engine_only=False, run_id="",
        reuse_yahoo_refresh=True, parent_managed_lifecycle=False,
    )


TERMINAL_EVENTS = {
    "STATUS_SUCCESS", "STATUS_SUCCESS_WITH_WARNING", "STATUS_WAITING_DATA",
    "STATUS_SKIPPED", "STATUS_FAILED",
}


def _run_post_market_case(tmp_path: Path, monkeypatch, stage) -> tuple[int, list[str], RunnerContext]:
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(run_sde_job, "parse_args", _args)
    monkeypatch.setattr(run_sde_job, "load_context", lambda **kwargs: ctx)
    monkeypatch.setattr(run_sde_job, "write_runtime_config_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_sde_job, "run_post_market_technical_stage", stage)
    monkeypatch.setitem(run_sde_job.JOBS, "full_manual", run_sde_job.job_post_market)
    code = run_sde_job.main()
    rows = [json.loads(line) for line in ctx.log_path.read_text(encoding="utf-8").splitlines()]
    events = [row["event"] for row in rows]
    terminals = [event for event in events if event in TERMINAL_EVENTS]
    assert len(terminals) == 1
    assert events.index(terminals[0]) < events.index("GLOBAL_RESOURCE_LOCK_RELEASED")
    assert events.index("FINAL_EXIT_CODE") < events.index("GLOBAL_RESOURCE_LOCK_RELEASED")
    assert events.index("GLOBAL_RESOURCE_LOCK_RELEASED") < events.index("JOB_LOCK_RELEASED")
    assert "LOCK_RELEASE_WITHOUT_TERMINAL_STATUS" not in events
    return code, events, ctx


def test_unexpected_stage_exception_writes_terminal_before_lock_release(tmp_path: Path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(run_sde_job, "parse_args", _args)
    monkeypatch.setattr(run_sde_job, "load_context", lambda **kwargs: ctx)
    monkeypatch.setattr(run_sde_job, "write_runtime_config_audit", lambda *args, **kwargs: None)
    monkeypatch.setitem(run_sde_job.JOBS, "full_manual", lambda current: (_ for _ in ()).throw(RuntimeError("boom")))

    assert run_sde_job.main() == 1
    events = [json.loads(line)["event"] for line in ctx.log_path.read_text(encoding="utf-8").splitlines()]
    terminals = [event for event in events if event in TERMINAL_EVENTS]
    assert terminals == ["STATUS_FAILED"]
    assert events.index("STATUS_FAILED") < events.index("GLOBAL_RESOURCE_LOCK_RELEASED")
    assert events.index("FINAL_EXIT_CODE") < events.index("GLOBAL_RESOURCE_LOCK_RELEASED")
    assert events.index("GLOBAL_RESOURCE_LOCK_RELEASED") < events.index("JOB_LOCK_RELEASED")
    status = json.loads((ctx.status_root / "full_manual_latest.json").read_text(encoding="utf-8"))
    assert status["exit_code"] == 1
    assert status["traceback_path"]
    assert Path(status["traceback_path"]).parent == ctx.status_root / "tracebacks"


def test_exception_before_zapi_request_is_terminal_and_releases_locks(tmp_path: Path, monkeypatch):
    def fail_before_request(_ctx):
        raise RuntimeError("exception before request")

    code, events, ctx = _run_post_market_case(tmp_path, monkeypatch, fail_before_request)
    assert code == 1
    assert "STATUS_FAILED" in events
    assert "POST_MARKET_STAGE_EXCEPTION" in events
    status = json.loads((ctx.status_root / "full_manual_latest.json").read_text(encoding="utf-8"))
    assert status["exit_code"] == 1
    assert status["traceback_path"]
    assert Path(status["traceback_path"]) == (
        ctx.status_root / "tracebacks" / f"{ctx.run_id}-post-market.txt"
    )


def test_missing_zapi_credentials_is_immediate_terminal_and_releases_locks(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TEST_ZAPI_KEY", raising=False)
    monkeypatch.delenv("TEST_ZAPI_URL", raising=False)
    cfg_path = tmp_path / "sources.json"
    cfg_path.write_text(json.dumps({
        "sources": {"ZAPI_IDX": {
            "enabled": True, "documentation_configured": True,
            "api_key_env": "TEST_ZAPI_KEY", "base_url_env": "TEST_ZAPI_URL",
        }},
    }), encoding="utf-8")

    def missing_credentials(current):
        result = validate_yahoo_against_zapi(
            historical_dir=tmp_path, symbols=["BBCA"], market_date="2026-08-04",
            output_dir=tmp_path / "out", config_path=cfg_path, blocking=True,
            event_callback=lambda event, detail: run_sde_job.append_job_log(
                current, event, json.dumps(detail)
            ),
        )
        raise SourceValidationBlocked(result)

    started = time.monotonic()
    code, events, _ = _run_post_market_case(tmp_path, monkeypatch, missing_credentials)
    assert time.monotonic() - started < 1.0
    assert code == 20
    assert "STATUS_WAITING_DATA" in events
    assert "ZAPI_SMOKE_TEST_FAILED" in events
    assert "ZAPI_RECONCILIATION_COMPLETE" in events
    assert "ZAPI_RETRY" not in events


@pytest.mark.parametrize("retry", [0, 2], ids=["http_timeout", "retry_exhausted"])
def test_zapi_timeout_paths_are_terminal_and_release_locks(tmp_path: Path, monkeypatch, retry: int):
    monkeypatch.setenv("TEST_ZAPI_KEY", "configured")
    monkeypatch.setenv("TEST_ZAPI_URL", "https://example.invalid")
    cfg_path = tmp_path / "sources.json"
    cfg_path.write_text(json.dumps({
        "sources": {"ZAPI_IDX": {
            "enabled": True, "documentation_configured": True,
            "api_key_env": "TEST_ZAPI_KEY", "base_url_env": "TEST_ZAPI_URL",
        }},
    }), encoding="utf-8")
    source = SourceConfig(
        name="ZAPI_IDX", enabled=True, documentation_configured=True,
        api_key_env="TEST_ZAPI_KEY", base_url_env="TEST_ZAPI_URL",
        retry=retry, backoff_base_seconds=0,
    )
    client = ZapiIdxClient(TimeoutTransport(), source)

    def timeout_stage(current):
        result = validate_yahoo_against_zapi(
            historical_dir=tmp_path, symbols=["BBCA"], market_date="2026-08-04",
            output_dir=tmp_path / "out", config_path=cfg_path, client=client, blocking=True,
            event_callback=lambda event, detail: run_sde_job.append_job_log(
                current, event, json.dumps(detail)
            ),
        )
        raise SourceValidationBlocked(result)

    code, events, _ = _run_post_market_case(tmp_path, monkeypatch, timeout_stage)
    assert code == 20
    assert "STATUS_WAITING_DATA" in events
    assert "ZAPI_SMOKE_TEST_FAILED" in events
    assert events.count("ZAPI_RETRY") == retry
    assert client.request_attempt_count == retry + 1


def test_unexpected_reconciliation_exception_is_terminal_and_releases_locks(tmp_path: Path, monkeypatch):
    def unexpected_reconciliation(_ctx):
        raise ValueError("unexpected reconciliation exception")

    code, events, ctx = _run_post_market_case(tmp_path, monkeypatch, unexpected_reconciliation)
    assert code == 1
    assert "STATUS_FAILED" in events
    assert "POST_MARKET_STAGE_EXCEPTION" in events
    status = json.loads((ctx.status_root / "full_manual_latest.json").read_text(encoding="utf-8"))
    assert "unexpected reconciliation exception" in status["details"]["error"]


def test_timeout_retry_exhausted_stops_at_smoke_and_logs_backoff(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_ZAPI_KEY", "configured")
    monkeypatch.setenv("TEST_ZAPI_URL", "https://example.invalid")
    cfg_path = tmp_path / "sources.json"
    cfg_path.write_text(json.dumps({
        "resolver_mode": "PRIMARY_WITH_FALLBACK",
        "record_ownership": {"DailyBar": {"primary": "ZAPI_IDX"}},
        "sources": {"ZAPI_IDX": {
            "enabled": True, "documentation_configured": True,
            "api_key_env": "TEST_ZAPI_KEY", "base_url_env": "TEST_ZAPI_URL",
        }},
    }), encoding="utf-8")
    source = SourceConfig(
        name="ZAPI_IDX", enabled=True, documentation_configured=True,
        api_key_env="TEST_ZAPI_KEY", base_url_env="TEST_ZAPI_URL",
        retry=2, backoff_base_seconds=0,
    )
    client = ZapiIdxClient(TimeoutTransport(), source)
    events: list[tuple[str, dict]] = []
    result = validate_yahoo_against_zapi(
        historical_dir=tmp_path, symbols=["BBCA", "TLKM"], market_date="2026-01-02",
        output_dir=tmp_path / "out", config_path=cfg_path, client=client, blocking=True,
        event_callback=lambda event, detail: events.append((event, detail)),
    )
    assert result["status"] == "FAILED_BLOCKING"
    assert result["request_count"] == 3
    assert sum(event == "ZAPI_RETRY" for event, _ in events) == 2
    assert sum(event == "ZAPI_SMOKE_TEST_FAILED" for event, _ in events) == 1
    assert not any(event == "ZAPI_BATCH_START" for event, _ in events)


def test_watchdog_emits_heartbeat(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with stage_watchdog(ctx, "TEST_STAGE", interval_seconds=0.05):
        time.sleep(0.12)
    rows = [json.loads(line) for line in ctx.log_path.read_text(encoding="utf-8").splitlines()]
    heartbeat = [row for row in rows if row["event"] == "STAGE_STILL_RUNNING"]
    assert heartbeat
    assert "current_stage" in heartbeat[0]["detail"]
