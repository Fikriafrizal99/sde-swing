from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "deploy" / "systemd"


def _text(path: str | Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_server_schedule_is_aligned_with_scheduler_config() -> None:
    scheduler = json.loads(_text("config/scheduler.json"))
    assert scheduler["timezone"] == "Asia/Jakarta"
    assert scheduler["market_outlook"]["time"] == "08:45"
    assert scheduler["post_market"]["time"] == "16:30"
    assert scheduler["final_watchlist"]["start_time"] == "18:00"

    assert "08:45:00 Asia/Jakarta" in _text("deploy/systemd/sde-swing-market-outlook.timer")
    assert "16:30:00 Asia/Jakarta" in _text("deploy/systemd/sde-swing-post-market.timer")
    assert "18:00:00 Asia/Jakarta" in _text("deploy/systemd/sde-swing-final-watchlist.timer")
    assert "18:45:00 Asia/Jakarta" in _text("deploy/systemd/sde-swing-position-management.timer")


def test_market_sensitive_timers_do_not_stale_catch_up() -> None:
    for filename in (
        "sde-swing-market-outlook.timer",
        "sde-swing-post-market.timer",
        "sde-swing-final-watchlist.timer",
        "sde-swing-position-management.timer",
    ):
        text = (SYSTEMD / filename).read_text(encoding="utf-8")
        assert "Persistent=false" in text

    assert "Persistent=true" in (SYSTEMD / "sde-swing-idx-universe.timer").read_text(
        encoding="utf-8"
    )


def test_idx_watcher_is_server_headless_chromium() -> None:
    cfg = json.loads(_text("config/idx_disclosure.json"))
    browser = cfg["request"]["browser"]
    assert browser["headless"] is True
    assert "chromium" in browser["channels"]

    service = _text("deploy/systemd/sde-swing-idx-watcher.service.template")
    assert "--watch --telegram --transport playwright" in service
    assert "Restart=always" in service


def test_server_broker_staging_is_linux_safe_and_stockbit_is_fail_closed() -> None:
    pipeline = json.loads(_text("config/pipeline.json"))
    assert pipeline["broker"]["downloads_dir"] == "data/runtime/broker_exports"
    assert "%USERPROFILE%" not in pipeline["broker"]["downloads_dir"]

    wrapper = _text("tools/run_server_scheduled_job.py")
    assert "STOCKBIT_PLAYWRIGHT_DISABLED" in wrapper
    assert "STOCKBIT_PROFILE_MISSING" in wrapper
    assert "STOCKBIT_PROFILE_EMPTY" in wrapper
    assert "run_scheduled_job.py" in wrapper


def test_server_final_watchlist_uses_3d_primary_and_keeps_today_pulse_contract() -> None:
    wrapper = _text("tools/run_server_scheduled_job.py")
    bridge = _text("tools/run_final_watchlist_playwright_bridge.py")

    assert 'FINAL_WATCHLIST_PRIMARY_PERIOD = "3D"' in wrapper
    assert 'result.extend(["--period", FINAL_WATCHLIST_PRIMARY_PERIOD])' in wrapper
    assert "TODAY" in bridge
    assert "exact real-1D capture" in bridge
    assert "PRIMARY is longer than 1D" in bridge


def test_news_is_chained_only_by_server_wrapper() -> None:
    wrapper = _text("tools/run_server_scheduled_job.py")
    assert '"market_outlook": "morning"' in wrapper
    assert '"post_market": "post_market"' in wrapper
    assert "news_monitor_market_impact" in wrapper
    assert 'classification != "SUCCESS"' in wrapper


def test_active_portfolio_server_runner_preserves_integrity_boundary() -> None:
    runner = _text("tools/run_server_position_management.py")
    assert "refresh_open_positions" in runner
    assert "portfolio_broker_daily" in runner
    assert "stockbit_playwright_collector" in runner
    assert "PORTFOLIO_BACKFILL_TASKS.csv" in runner
    assert "portfolio_broker_exports" in runner
    assert '"import"' in runner
    assert "position_management_runtime_integrity" in runner
    assert "repair_legacy_broker_backfill_provenance" in runner
    assert "portfolio_delivery_status" in runner
    assert "decision_engine" not in runner.lower()

    service = _text("deploy/systemd/sde-swing-position-management.service.template")
    assert "run_server_position_management.py" in service


def test_idx_universe_refresh_does_not_rewrite_pipeline_config() -> None:
    service = _text("deploy/systemd/sde-swing-idx-universe.service.template")
    assert "tools/update_idx_universe.py" in service
    assert "--activate" not in service
    assert "Fri *-*-* 20:00:00 Asia/Jakarta" in _text(
        "deploy/systemd/sde-swing-idx-universe.timer"
    )


def test_installer_manages_complete_server_unit_set() -> None:
    installer = _text("scripts/install_systemd_services.sh")
    for name in (
        "sde-swing-idx-watcher.service",
        "sde-swing-market-outlook.timer",
        "sde-swing-post-market.timer",
        "sde-swing-final-watchlist.timer",
        "sde-swing-position-management.timer",
        "sde-swing-idx-universe.timer",
    ):
        assert name in installer
    assert "data/runtime/broker_exports" in installer
    assert "data/runtime/portfolio_broker_exports" in installer
    assert "playwright install --with-deps chromium" in installer
    assert "stockbit_playwright_collector setup" in installer
    assert "stockbit_playwright_collector enable" in installer
