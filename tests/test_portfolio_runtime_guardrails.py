from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.portfolio import refresh_open_positions as refresh
from modules.telegram.router import TelegramRouter
from tools import check_telegram_report_route as route_checker
from tools.check_telegram_report_route import effective_thread


ROOT = Path(__file__).resolve().parents[1]
WIB = ZoneInfo("Asia/Jakarta")


def _write_closed_history(path: Path, symbol: str, day: str, *, close: str = "100") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Symbol,Ticker,Date,Open,High,Low,Close,Adj Close,Volume\n"
        f"{symbol},{symbol}.JK,{day},99,102,98,{close},{close},1000000\n",
        encoding="utf-8",
    )
    closed_mtime = datetime.fromisoformat(f"{day}T17:00:00").replace(tzinfo=WIB).timestamp()
    os.utime(path, (closed_mtime, closed_mtime))


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


def test_report_checker_keeps_generic_report_distinct_from_market_scheduler_topics() -> None:
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
                "report": "107",
            }
        }
    }
    router = TelegramRouter(config, {"TELEGRAM_THREAD_REPORT_ID": "107"})

    report_thread = effective_thread(router, scheduler, "position_management", "report")
    market_thread = effective_thread(router, scheduler, "market_outlook", "market_outlook")
    post_thread = effective_thread(router, scheduler, "post_market", "post_market")

    assert report_thread == "107"
    assert market_thread == "9"
    assert post_thread == "9"
    assert report_thread not in {market_thread, post_thread}


def test_definitive_topic_validation_requires_real_send_message(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class SendResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"ok": True, "result": {"message_id": 555}}

    class DeleteResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"ok": True, "result": True}

    def fake_post(url, data, timeout):
        calls.append((url, dict(data)))
        return DeleteResponse() if url.endswith("/deleteMessage") else SendResponse()

    monkeypatch.setattr(route_checker.requests, "post", fake_post)
    ok, detail = route_checker.definitive_validate_topic("secret-token", "-100123", "107")

    assert ok is True
    assert detail == "VALID"
    assert calls[0][0].endswith("/sendMessage")
    assert calls[0][1]["chat_id"] == "-100123"
    assert calls[0][1]["message_thread_id"] == "107"
    assert calls[0][1]["disable_notification"] == "true"
    assert calls[1][0].endswith("/deleteMessage")
    assert calls[1][1]["message_id"] == "555"


def test_definitive_topic_validation_rejects_message_thread_not_found(monkeypatch) -> None:
    class Response:
        ok = False
        status_code = 400

        @staticmethod
        def json() -> dict:
            return {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message thread not found",
            }

    monkeypatch.setattr(route_checker.requests, "post", lambda *args, **kwargs: Response())
    ok, detail = route_checker.definitive_validate_topic("secret-token", "-100123", "107")

    assert ok is False
    assert detail == "Bad Request: message thread not found"


def test_topic_configuration_auto_detects_and_validates_before_setx() -> None:
    source = (ROOT / "maintenance/CONFIGURE_TELEGRAM_TOPICS.bat").read_text(encoding="utf-8-sig")
    validate_pos = source.index("tools\\check_telegram_report_route.py")
    save_pos = source.index("setx TELEGRAM_THREAD_REPORT_ID")

    assert validate_pos < save_pos
    assert "Auto-detect REPORT TEST + Validate + Simpan" in source
    assert "detect_telegram_report_topic.py" in source
    assert "--definitive" in source
    assert "TIDAK disimpan karena sendMessage Telegram gagal" in source


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


def test_current_portfolio_history_skips_probe_and_downloader(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    historical = tmp_path / "historical/by_symbol"
    for symbol in ("BBCA", "MDKA"):
        _write_closed_history(historical / f"{symbol}.csv", symbol, "2026-08-13")
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(
        json.dumps(
            {
                "paths": {"historical_dir": str(historical)},
                "data_freshness": {"market_close": "16:15"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        refresh,
        "parse_args",
        lambda: argparse.Namespace(config=str(config_path), trade_date="2026-08-13"),
    )
    monkeypatch.setattr(
        refresh,
        "read_open_portfolio_symbols",
        lambda _db_path: ["BBCA", "MDKA"],
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Yahoo probe/downloader must not run for current local candles")

    monkeypatch.setattr(refresh, "probe_yahoo_runtime", forbidden)
    monkeypatch.setattr(refresh.subprocess, "run", forbidden)

    assert refresh.main() == 0
    assert "SKIPPED_ALREADY_CURRENT" in capsys.readouterr().out


def test_portfolio_history_fast_path_fails_closed_for_partial_or_preclose_rows(
    tmp_path: Path,
) -> None:
    historical = tmp_path / "historical"
    _write_closed_history(historical / "BBCA.csv", "BBCA", "2026-08-13")
    _write_closed_history(
        historical / "MDKA.csv",
        "MDKA",
        "2026-08-13",
        close="",
    )

    current, evidence = refresh.local_portfolio_history_current(
        historical,
        ["BBCA", "MDKA"],
        trade_date="2026-08-13",
        market_close="16:15",
    )
    assert current is False
    assert evidence == {"BBCA": "VALID_CLOSED_CANDLE", "MDKA": "CLOSE_INVALID"}

    preclose = datetime(2026, 8, 13, 15, 0, tzinfo=WIB).timestamp()
    os.utime(historical / "BBCA.csv", (preclose, preclose))
    current, evidence = refresh.local_portfolio_history_current(
        historical,
        ["BBCA"],
        trade_date="2026-08-13",
        market_close="16:15",
    )
    assert current is False
    assert evidence["BBCA"] == "FILE_WRITTEN_BEFORE_MARKET_CLOSE"


def test_one_stale_portfolio_symbol_runs_existing_probe_and_downloader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    historical = tmp_path / "historical/by_symbol"
    _write_closed_history(historical / "BBCA.csv", "BBCA", "2026-08-13")
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(
        json.dumps(
            {
                "paths": {
                    "historical_dir": str(historical),
                    "historical_downloader": str(tmp_path / "downloader.py"),
                    "manifest_dir": str(tmp_path / "manifests"),
                },
                "data_freshness": {"market_close": "16:15"},
                "download": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        refresh,
        "parse_args",
        lambda: argparse.Namespace(config=str(config_path), trade_date="2026-08-13"),
    )
    monkeypatch.setattr(
        refresh,
        "read_open_portfolio_symbols",
        lambda _db_path: ["BBCA", "MDKA"],
    )
    calls = {"probe": 0, "downloader": 0}

    def successful_probe():
        calls["probe"] += 1
        return True, "OK"

    def successful_downloader(*_args, **_kwargs):
        calls["downloader"] += 1
        return subprocess.CompletedProcess(args=[], returncode=0)

    monkeypatch.setattr(refresh, "probe_yahoo_runtime", successful_probe)
    monkeypatch.setattr(refresh, "write_symbols", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(refresh, "make_run_id", lambda **_kwargs: "TEST-RUN")
    monkeypatch.setattr(refresh.subprocess, "run", successful_downloader)

    assert refresh.main() == 0
    assert calls == {"probe": 1, "downloader": 1}


def test_no_open_positions_keeps_existing_skip_behavior(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path = tmp_path / "pipeline.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        refresh,
        "parse_args",
        lambda: argparse.Namespace(config=str(config_path), trade_date="2026-08-13"),
    )
    monkeypatch.setattr(refresh, "read_open_portfolio_symbols", lambda _db_path: [])
    monkeypatch.setattr(
        refresh,
        "probe_yahoo_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )

    assert refresh.main() == 0
    assert "SKIPPED_NO_OPEN_POSITION" in capsys.readouterr().out


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
