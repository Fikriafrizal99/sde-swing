from argparse import Namespace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools import run_final_watchlist_entrypoint as entrypoint
from tools import run_full_daily_broker_period as full_daily


ROOT = Path(__file__).resolve().parents[1]


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


def _full_daily_args(trade_date: str = "") -> Namespace:
    return Namespace(
        config="config/pipeline.json",
        scheduler_config="config/scheduler.json",
        trade_date=trade_date,
        period="1D",
        custom_start="",
        timeout=-1,
        no_telegram=False,
        debug=False,
    )


def test_full_daily_uses_canonical_date_resolver(monkeypatch):
    captured = {}

    def fake_resolver(argv):
        captured["argv"] = list(argv)
        return "2026-08-14"

    monkeypatch.setattr(full_daily, "resolve_effective_trade_date", fake_resolver)
    resolved = full_daily.resolve_full_daily_trade_date(_full_daily_args())

    assert resolved == "2026-08-14"
    assert "--scheduler-config" in captured["argv"]
    assert "--trade-date" not in captured["argv"]


def test_full_daily_preserves_explicit_replay_date(monkeypatch):
    captured = {}

    def fake_resolver(argv):
        captured["argv"] = list(argv)
        return "2026-08-13"

    monkeypatch.setattr(full_daily, "resolve_effective_trade_date", fake_resolver)
    resolved = full_daily.resolve_full_daily_trade_date(_full_daily_args("2026-08-13"))

    assert resolved == "2026-08-13"
    index = captured["argv"].index("--trade-date")
    assert captured["argv"][index + 1] == "2026-08-13"


def test_full_daily_final_watchlist_routes_through_lifecycle_entrypoint():
    command = full_daily.final_watchlist_command(_full_daily_args(), "2026-08-14")
    rendered = " ".join(command).replace("\\", "/")

    assert "tools/run_final_watchlist_entrypoint.py" in rendered
    assert "tools/run_final_watchlist_broker_period.py" not in rendered
    index = command.index("--trade-date")
    assert command[index + 1] == "2026-08-14"


def test_scheduler_final_watchlist_routes_through_lifecycle_entrypoint():
    source = (ROOT / "scheduler" / "SCHEDULE_FINAL_WATCHLIST.bat").read_text(encoding="utf-8")
    normalized = source.replace("\\", "/")

    assert "tools/run_final_watchlist_entrypoint.py --period 1D" in normalized
    assert "tools/run_final_watchlist_broker_period.py --period 1D" not in normalized


def test_preview_existing_resolves_completed_trade_date_before_integrated_runner():
    source = (ROOT / "RUN_FINAL_WATCHLIST.bat").read_text(encoding="utf-8")
    normalized = source.replace("\\", "/")

    assert "tools/resolve_last_trading_day.py" in normalized
    assert "--job final_watchlist --trade-date !PREVIEW_DATE! --preview-existing --no-telegram" in normalized
