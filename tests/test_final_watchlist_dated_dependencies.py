from datetime import date
import json

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


def _status(job: str, status: str, trade_date: str, *, config_version: str = "TEST-CONFIG"):
    return {
        "job": job,
        "status": status,
        "status_v1_7": status,
        "trade_date": trade_date,
        "config_version": config_version,
    }


def _write_dated(ctx, job: str, payload: dict, suffix: str = "run"):
    root = ctx.paths.resolve("status") / ctx.trade_date.isoformat()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{job}_{suffix}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_final_watchlist_uses_effective_trade_date_status_when_latest_is_later(tmp_path):
    ctx = _context(tmp_path)
    for job in ("market_outlook", "post_market"):
        _write_dated(ctx, job, _status(job, "SUCCESS", "2026-08-14"))

    statuses = {
        "market_outlook": _status("market_outlook", "SKIPPED", "2026-08-15"),
        "post_market": _status("post_market", "SKIPPED", "2026-08-15"),
        "broker_summary": _status("broker_summary", "SUCCESS_WITH_WARNING", "2026-08-14"),
        "broker_multi_day": _status("broker_multi_day", "SUCCESS", "2026-08-14"),
    }

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is True
    assert result["dependencies"]["market_outlook"]["trade_date"] == "2026-08-14"
    assert result["dependencies"]["post_market"]["trade_date"] == "2026-08-14"


def test_final_watchlist_does_not_resurrect_older_success_over_same_date_failure(tmp_path):
    ctx = _context(tmp_path)
    _write_dated(ctx, "market_outlook", _status("market_outlook", "SUCCESS", "2026-08-14"))

    statuses = {
        "market_outlook": _status("market_outlook", "FAILED", "2026-08-14"),
        "post_market": _status("post_market", "SUCCESS", "2026-08-14"),
        "broker_summary": _status("broker_summary", "SUCCESS", "2026-08-14"),
        "broker_multi_day": _status("broker_multi_day", "SUCCESS", "2026-08-14"),
    }

    result = validate_dependency_status(ctx, "final_watchlist", statuses)

    assert result["valid"] is False
    assert result["dependencies"]["market_outlook"]["status"] == "FAILED"


def test_other_jobs_keep_existing_latest_status_contract(tmp_path):
    ctx = _context(tmp_path)
    _write_dated(ctx, "broker_summary", _status("broker_summary", "SUCCESS", "2026-08-14"))

    statuses = {
        "broker_summary": _status("broker_summary", "SKIPPED", "2026-08-15"),
    }

    result = validate_dependency_status(ctx, "broker_multi_day", statuses)

    assert result["valid"] is False
    assert result["dependencies"]["broker_summary"]["trade_date"] == "2026-08-15"
