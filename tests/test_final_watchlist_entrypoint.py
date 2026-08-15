from datetime import datetime
from zoneinfo import ZoneInfo

from tools import run_final_watchlist_entrypoint as entrypoint


def test_weekend_normal_resolves_to_last_completed_idx_session():
    resolved = entrypoint.resolve_effective_trade_date(
        [],
        now=datetime(2026, 8, 15, 23, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert resolved == "2026-08-14"


def test_explicit_trade_date_is_preserved():
    resolved = entrypoint.resolve_effective_trade_date(
        ["--trade-date", "2026-08-13"],
        now=datetime(2026, 8, 15, 23, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert resolved == "2026-08-13"


def test_preflight_failure_replaces_stale_success(monkeypatch, tmp_path):
    status_path = tmp_path / "final_watchlist_latest.json"
    monkeypatch.setattr(entrypoint, "STATUS_PATH", status_path)

    previous = {
        "run_id": "SWING-OLD",
        "trade_date": "2026-08-13",
        "status": "SUCCESS",
    }
    entrypoint.write_orchestration_failure(
        trade_date="2026-08-14",
        exit_code=1,
        started_at=datetime(2026, 8, 15, 23, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
        previous_status=previous,
        observed_status=previous,
    )

    payload = entrypoint.read_json(status_path)
    assert payload["status"] == "FAILED"
    assert payload["trade_date"] == "2026-08-14"
    assert payload["details"]["previous_status_run_id"] == "SWING-OLD"
    assert payload["details"]["reason"] == "FINAL_WATCHLIST_ORCHESTRATION_FAILED_BEFORE_STATUS_UPDATE"


def test_current_engine_failure_is_not_overwritten(monkeypatch, tmp_path):
    status_path = tmp_path / "final_watchlist_latest.json"
    monkeypatch.setattr(entrypoint, "STATUS_PATH", status_path)

    previous = {
        "run_id": "SWING-OLD",
        "trade_date": "2026-08-13",
        "status": "SUCCESS",
    }
    observed = {
        "run_id": "SWING-NEW",
        "trade_date": "2026-08-14",
        "status": "FAILED",
        "details": {"reason": "ENGINE_SPECIFIC_FAILURE"},
    }
    entrypoint.atomic_write_json(status_path, observed)

    entrypoint.write_orchestration_failure(
        trade_date="2026-08-14",
        exit_code=1,
        started_at=datetime(2026, 8, 15, 23, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
        previous_status=previous,
        observed_status=observed,
    )

    assert entrypoint.read_json(status_path) == observed


def test_running_status_is_terminalized_on_child_failure(monkeypatch, tmp_path):
    status_path = tmp_path / "final_watchlist_latest.json"
    monkeypatch.setattr(entrypoint, "STATUS_PATH", status_path)

    previous = {
        "run_id": "SWING-OLD",
        "trade_date": "2026-08-13",
        "status": "SUCCESS",
    }
    observed = {
        "run_id": "SWING-NEW",
        "trade_date": "2026-08-14",
        "status": "RUNNING",
    }
    entrypoint.atomic_write_json(status_path, observed)

    entrypoint.write_orchestration_failure(
        trade_date="2026-08-14",
        exit_code=1,
        started_at=datetime(2026, 8, 15, 23, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
        previous_status=previous,
        observed_status=observed,
    )

    payload = entrypoint.read_json(status_path)
    assert payload["status"] == "FAILED"
    assert payload["trade_date"] == "2026-08-14"
