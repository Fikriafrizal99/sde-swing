from __future__ import annotations

import subprocess
from pathlib import Path

from modules.portfolio import refresh_open_positions as refresh
from modules.telegram.router import TelegramRouter
from tools.check_telegram_report_route import effective_thread


ROOT = Path(__file__).resolve().parents[1]


def test_symbolic_telegram_ui_labels_are_not_message_thread_ids() -> None:
    config = {
        "telegram_ui": {
            "topic_routing": {
                "market_outlook": "REPORT",
                "post_market": "REPORT",
            }
        }
    }
    router = TelegramRouter(config, {"TELEGRAM_THREAD_REPORT_ID": "9"})

    market = router.resolve("market_outlook", "market_outlook")
    post = router.resolve("post_market", "post_market")

    assert market.message_thread_id == ""
    assert market.fallback_to_main_chat is True
    assert post.message_thread_id == ""
    assert post.fallback_to_main_chat is True


def test_report_checker_exposes_conflict_after_symbolic_ui_falls_back_to_scheduler() -> None:
    config = {
        "telegram_ui": {
            "topic_routing": {
                "market_outlook": "REPORT",
                "post_market": "REPORT",
            }
        }
    }
    scheduler = {
        "delivery": {
            "topic_routing": {
                "market_outlook": "9",
                "post_market": "9",
                "report": "",
            }
        }
    }
    router = TelegramRouter(config, {"TELEGRAM_THREAD_REPORT_ID": "9"})

    report_thread = effective_thread(router, scheduler, "position_management", "report")
    market_thread = effective_thread(router, scheduler, "market_outlook", "market_outlook")
    post_thread = effective_thread(router, scheduler, "post_market", "post_market")

    assert report_thread == "9"
    assert market_thread == "9"
    assert post_thread == "9"
    assert report_thread in {market_thread, post_thread}


def test_yahoo_runtime_probe_times_out_instead_of_hanging(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(refresh.subprocess, "run", fake_run)
    ok, detail = refresh.probe_yahoo_runtime(timeout_seconds=3)

    assert ok is False
    assert "timeout >3s" in detail


def test_yahoo_runtime_probe_reports_success(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["python"],
        returncode=0,
        stdout="OK|1.0|7.0\n",
        stderr="",
    )
    monkeypatch.setattr(refresh.subprocess, "run", lambda *args, **kwargs: completed)

    ok, detail = refresh.probe_yahoo_runtime(timeout_seconds=3)

    assert ok is True
    assert detail == "OK|1.0|7.0"


def test_maintenance_exposes_isolated_yahoo_runtime_repair() -> None:
    menu = (ROOT / "maintenance/MAINTENANCE_MENU.bat").read_text(encoding="utf-8-sig")
    repair = (ROOT / "maintenance/REPAIR_YAHOO_RUNTIME.bat").read_text(encoding="utf-8-sig")

    assert "[7] Repair Yahoo/YFinance runtime" in menu
    assert "maintenance\\REPAIR_YAHOO_RUNTIME.bat" in menu
    assert "--force-reinstall" in repair
    assert "protobuf>=5,<8" in repair
    assert "yfinance>=0.2.40" in repair
    assert "Decision" in repair
    assert "Final Watchlist" in repair
