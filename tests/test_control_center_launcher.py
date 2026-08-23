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
    assert "SDE SWING V1.7.1 - CONTROL CENTER" in source
    assert "maintenance\\DAILY_OPERATIONS_MENU.bat" in source
    assert "maintenance\\BROKER_MENU.bat" in source
    assert "maintenance\\PORTFOLIO_MENU.bat" in source
    assert "maintenance\\SYSTEM_MENU.bat" in source
    assert "maintenance\\PERFORMANCE_MENU.bat" in source
    assert "maintenance\\MAINTENANCE_MENU.bat" in source


def test_primary_job_submenus_keep_current_runtime_wrappers_and_existing_controls() -> None:
    expected_runners = {
        "RUN_MARKET_OUTLOOK.bat": "run_sde_job_integrated.py",
        "RUN_POST_MARKET.bat": "run_sde_job_integrated_market_first.py",
        "RUN_FINAL_WATCHLIST.bat": "run_final_watchlist_entrypoint.py",
    }
    for filename, runner in expected_runners.items():
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert runner in source
        assert "run_sde_job.py" not in source

    market = (ROOT / "RUN_MARKET_OUTLOOK.bat").read_text(encoding="utf-8")
    post = (ROOT / "RUN_POST_MARKET.bat").read_text(encoding="utf-8")
    for source in (market, post):
        assert "Preview existing" in source
        assert "Kirim ulang - delivery-only" in source
    assert "Morning News" in market
    assert "Post Market News" in post

    daily = (ROOT / "maintenance/DAILY_OPERATIONS_MENU.bat").read_text(encoding="utf-8")
    broker = (ROOT / "maintenance/BROKER_MENU.bat").read_text(encoding="utf-8")
    assert "run_full_daily_broker_period.py" in daily
    assert "run_sde_job_integrated.py" in broker


def test_scheduler_keeps_report_separate_and_news_has_own_topic() -> None:
    scheduler = json.loads((ROOT / "config/scheduler.json").read_text(encoding="utf-8"))
    routing = scheduler["delivery"]["topic_routing"]

    assert routing["market_outlook"] == "9"
    assert routing["post_market"] == "9"
    assert routing["report"] == "701"
    assert routing["report"] != routing["market_outlook"]
    assert routing["news"] == "1451"
    assert routing["morning_news"] == "1451"
    assert routing["post_market_news"] == "1451"


def test_portfolio_delivery_has_dedicated_route_guard() -> None:
    launcher = (ROOT / "maintenance/RUN_POSITION_MANAGEMENT.bat").read_text(encoding="utf-8")
    checker = (ROOT / "tools/check_telegram_report_route.py").read_text(encoding="utf-8")

    assert "check_telegram_report_route.py" in launcher
    assert "--no-telegram" in launcher
    assert "TELEGRAM_THREAD_REPORT_ID" in checker
    assert "Market/Post Market" in checker
