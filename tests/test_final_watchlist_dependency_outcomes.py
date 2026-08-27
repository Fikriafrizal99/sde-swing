from datetime import date
import json

import pytest

from modules.runtime.context import RuntimeContext
from modules.runtime.jobs import validate_dependency_status


def _context(tmp_path):
    return RuntimeContext(
        job_name="final_watchlist",
        trade_date=date(2026, 8, 14),
        run_id="TEST-FW",
        config={},
        scheduler_config={"paths": {"job_status_root": "status"}},
        root=tmp_path,
        config_version="TEST-CONFIG",
    )


def _status(
    job: str,
    status: str,
    trade_date: str = "2026-08-14",
    *,
    stage: str = "",
    config_version: str = "TEST-CONFIG",
):
    payload = {
        "job": job,
        "status": status,
        "status_v1_7": status,
        "trade_date": trade_date,
        "config_version": config_version,
    }
    if stage:
        payload["stage"] = stage
    return payload


def _base_statuses():
    return {
        "market_outlook": _status("market_outlook", "SUCCESS", stage="MARKET_OUTLOOK"),
        "post_market": _status("post_market", "SUCCESS", stage="POST_MARKET"),
        "broker_summary": _status("broker_summary", "SUCCESS_WITH_WARNING", stage="BROKER_SUMMARY"),
    }


@pytest.mark.parametrize(
    ("dependency", "stage"),
    [
        ("market_outlook", "MARKET_OUTLOOK"),
        ("post_market", "POST_MARKET"),
    ],
)
def test_final_watchlist_accepts_delivery_failure_after_completed_engine_stage(
    tmp_path,
    dependency,
    stage,
):
    ctx = _context(tmp_path)
    statuses = _base_statuses()
    statuses[dependency] = _status(dependency, "DELIVERY_FAILED", stage=stage)

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is True
    item = result["dependencies"][dependency]
    assert item["status"] == "SUCCESS_WITH_WARNING"
    assert item["source_status"] == "DELIVERY_FAILED"
    assert item["dependency_status_override"] == "ENGINE_COMPLETE_DELIVERY_FAILED"
    assert item["date_match"] is True
    assert item["config_match"] is True


@pytest.mark.parametrize("dependency", ["market_outlook", "post_market"])
def test_final_watchlist_accepts_normalized_runtime_delivery_failure_shape(tmp_path, dependency):
    ctx = _context(tmp_path)
    statuses = _base_statuses()
    statuses[dependency] = {
        "job": dependency,
        "run_id": f"RUN-{dependency}",
        "status": "FAILED",
        "status_v1_7": "FAILED",
        "legacy_status": "DELIVERY_FAILED",
        "current_stage": "ENHANCED_REPORT_DELIVERY",
        "engine_status": "SUCCESS",
        "delivery_status": "DELIVERY_FAILED",
        "trade_date": "2026-08-14",
        "config_version": "TEST-CONFIG",
        "details": {
            "engine_status": "SUCCESS",
            "report_status": "SUCCESS",
            "delivery_status": "DELIVERY_FAILED",
        },
    }

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is True
    item = result["dependencies"][dependency]
    assert item["status"] == "SUCCESS_WITH_WARNING"
    assert item["source_status"] == "DELIVERY_FAILED"
    assert item["dependency_status_override"] == "ENGINE_COMPLETE_DELIVERY_FAILED"


def test_final_watchlist_recovers_exact_child_engine_result_after_parent_start_status(tmp_path):
    ctx = _context(tmp_path)
    statuses = _base_statuses()
    run_id = "SDE-POST-MARKET-TEST"
    statuses["post_market"] = {
        "job": "post_market",
        "run_id": run_id,
        "status": "FAILED",
        "status_v1_7": "FAILED",
        "current_stage": "START",
        "engine_status": "RUNNING",
        "delivery_status": "NOT_RUN",
        "exit_code": 0,
        "trade_date": "2026-08-14",
        "config_version": "TEST-CONFIG",
        "details": {"config_audit_path": "runtime-config.json"},
    }
    status_root = ctx.paths.resolve("status")
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / f"engine_result_{run_id}.json").write_text(
        json.dumps({
            "status": "SUCCESS",
            "stage": "POST_MARKET",
            "exit_code": 0,
            "details": {"snapshot_id": "TECH-20260814"},
        }),
        encoding="utf-8",
    )

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is True
    item = result["dependencies"]["post_market"]
    assert item["status"] == "SUCCESS"
    assert item["source_status"] == "FAILED"
    assert item["dependency_status_override"] == "ENGINE_RESULT_RECOVERY"


def test_final_watchlist_does_not_recover_wrong_engine_stage(tmp_path):
    ctx = _context(tmp_path)
    statuses = _base_statuses()
    run_id = "SDE-POST-MARKET-WRONG-STAGE"
    statuses["post_market"] = {
        "job": "post_market",
        "run_id": run_id,
        "status": "FAILED",
        "status_v1_7": "FAILED",
        "current_stage": "START",
        "engine_status": "RUNNING",
        "delivery_status": "NOT_RUN",
        "exit_code": 0,
        "trade_date": "2026-08-14",
        "config_version": "TEST-CONFIG",
    }
    status_root = ctx.paths.resolve("status")
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / f"engine_result_{run_id}.json").write_text(
        json.dumps({"status": "SUCCESS", "stage": "POST_MARKET_EXCEPTION", "exit_code": 0}),
        encoding="utf-8",
    )

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is False
    assert result["dependencies"]["post_market"]["status"] == "FAILED"


def test_final_watchlist_reuses_completed_same_date_status_after_dependency_skip(tmp_path):
    ctx = _context(tmp_path)
    dated_root = ctx.paths.resolve("status") / ctx.trade_date.isoformat()
    dated_root.mkdir(parents=True, exist_ok=True)
    previous = _status("market_outlook", "SUCCESS", stage="MARKET_OUTLOOK")
    (dated_root / "market_outlook_completed.json").write_text(
        json.dumps(previous),
        encoding="utf-8",
    )

    statuses = _base_statuses()
    statuses["market_outlook"] = _status(
        "market_outlook",
        "SKIPPED",
        stage="DEPENDENCY_VALIDATION",
    )

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is True
    item = result["dependencies"]["market_outlook"]
    assert item["status"] == "SUCCESS"
    assert item["source_status"] == "SKIPPED"
    assert item["dependency_status_override"] == "SAME_DATE_SKIPPED_REUSE_COMPLETED_STATUS"


def test_final_watchlist_does_not_hide_real_same_date_engine_failure(tmp_path):
    ctx = _context(tmp_path)
    dated_root = ctx.paths.resolve("status") / ctx.trade_date.isoformat()
    dated_root.mkdir(parents=True, exist_ok=True)
    previous = _status("post_market", "SUCCESS", stage="POST_MARKET")
    (dated_root / "post_market_completed.json").write_text(
        json.dumps(previous),
        encoding="utf-8",
    )

    statuses = _base_statuses()
    statuses["post_market"] = _status(
        "post_market",
        "FAILED",
        stage="POST_MARKET_EXCEPTION",
    )

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is False
    item = result["dependencies"]["post_market"]
    assert item["status"] == "FAILED"
    assert item["fresh"] is False


def test_other_jobs_keep_delivery_failure_strict(tmp_path):
    ctx = _context(tmp_path)
    statuses = {
        "market_outlook": _status(
            "market_outlook",
            "DELIVERY_FAILED",
            stage="MARKET_OUTLOOK",
        )
    }

    result = validate_dependency_status(ctx, "post_market", statuses)

    assert result["valid"] is False
    assert result["dependencies"]["market_outlook"]["status"] == "DELIVERY_FAILED"
