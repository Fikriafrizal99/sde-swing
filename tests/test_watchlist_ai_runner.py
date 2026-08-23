from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from modules.job_runner.reports import ReportPayload
from tools import run_final_watchlist_entrypoint as final_entry
from tools import run_watchlist_ai


def test_ai_delivery_refuses_main_chat_when_ai_topic_missing(monkeypatch):
    ctx = SimpleNamespace(trade_date=date(2026, 8, 21))
    payload = ReportPayload(
        report_type="watchlist_ai_status",
        filename="watchlist_ai_status.txt",
        text="info",
        topic="watchlist_ai",
    )

    monkeypatch.setattr(
        run_watchlist_ai,
        "telegram_route",
        lambda _ctx, _payload: {"category": "AI", "message_thread_id": ""},
    )

    def should_not_send(*args, **kwargs):
        raise AssertionError("deliver() must not be called without a dedicated AI topic")

    monkeypatch.setattr(run_watchlist_ai, "deliver", should_not_send)
    result = run_watchlist_ai._deliver_ai(ctx, [payload])

    assert result[0]["status"] == "SKIPPED_AI_TOPIC_NOT_CONFIGURED"
    assert result[0]["required_env"] == "TELEGRAM_THREAD_AI_ID"


def test_final_watchlist_entrypoint_launches_ai_only_after_official_success(monkeypatch):
    calls = []

    class Completed:
        returncode = 0

    monkeypatch.setattr(final_entry.subprocess, "run", lambda *args, **kwargs: Completed())
    monkeypatch.setattr(final_entry, "_run_watchlist_ai", lambda forwarded, trade_date: calls.append((forwarded, trade_date)))

    rc = final_entry.main(["--trade-date", "2026-08-21"])

    assert rc == 0
    assert len(calls) == 1
    assert calls[0][1] == "2026-08-21"


def test_final_watchlist_entrypoint_does_not_launch_ai_after_official_failure(monkeypatch):
    calls = []

    class Completed:
        returncode = 1

    monkeypatch.setattr(final_entry.subprocess, "run", lambda *args, **kwargs: Completed())
    monkeypatch.setattr(final_entry, "_run_watchlist_ai", lambda *args, **kwargs: calls.append(True))
    monkeypatch.setattr(final_entry, "write_orchestration_failure", lambda **kwargs: None)

    rc = final_entry.main(["--trade-date", "2026-08-21"])

    assert rc == 1
    assert calls == []
