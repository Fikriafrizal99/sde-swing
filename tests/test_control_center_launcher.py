from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_run_sde_is_the_single_top_level_control_center() -> None:
    launcher = ROOT / "RUN_SDE.bat"
    duplicate = ROOT / "START_SDE_SWING.bat"
    source = launcher.read_text(encoding="utf-8")

    assert launcher.exists()
    assert not duplicate.exists()
    assert "V1.7.0 MULTI-SOURCE - CONTROL CENTER" in source
    assert "maintenance\\DAILY_OPERATIONS_MENU.bat" in source
    assert "maintenance\\BROKER_MENU.bat" in source
    assert "maintenance\\PORTFOLIO_MENU.bat" in source
    assert "maintenance\\SYSTEM_MENU.bat" in source
    assert "maintenance\\PERFORMANCE_MENU.bat" in source
    assert "maintenance\\MAINTENANCE_MENU.bat" in source


def test_primary_job_submenus_use_integrated_runner_not_legacy_runner() -> None:
    for filename in ("RUN_MARKET_OUTLOOK.bat", "RUN_POST_MARKET.bat", "RUN_FINAL_WATCHLIST.bat"):
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "run_sde_job_integrated.py" in source
        assert "run_sde_job.py" not in source

    daily = (ROOT / "maintenance/DAILY_OPERATIONS_MENU.bat").read_text(encoding="utf-8")
    broker = (ROOT / "maintenance/BROKER_MENU.bat").read_text(encoding="utf-8")
    assert "run_sde_job_integrated.py" in daily
    assert "run_sde_job_integrated.py" in broker


def test_scheduler_does_not_alias_generic_report_to_market_thread() -> None:
    scheduler = json.loads((ROOT / "config/scheduler.json").read_text(encoding="utf-8"))
    routing = scheduler["delivery"]["topic_routing"]

    assert routing["market_outlook"] == "9"
    assert routing["post_market"] == "9"
    assert routing["report"] == ""


def test_portfolio_delivery_has_dedicated_route_guard() -> None:
    launcher = (ROOT / "maintenance/RUN_POSITION_MANAGEMENT.bat").read_text(encoding="utf-8")
    checker = (ROOT / "tools/check_telegram_report_route.py").read_text(encoding="utf-8")

    assert "check_telegram_report_route.py" in launcher
    assert "--no-telegram" in launcher
    assert "TELEGRAM_THREAD_REPORT_ID" in checker
    assert "Market/Post Market" in checker
