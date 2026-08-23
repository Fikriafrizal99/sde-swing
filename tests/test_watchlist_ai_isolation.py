from __future__ import annotations

from modules.ai_interpretation import GeminiInterpreter
from modules.telegram.router import TelegramRouter
from modules.telegram.watchlist_ai_ui import (
    format_watchlist_ai_failure,
    format_watchlist_ai_interpretation,
)


def test_legacy_report_interpreter_no_longer_spends_final_watchlist_budget():
    interpreter = GeminiInterpreter()
    assert interpreter.max_watchlist_calls == 0


def test_watchlist_ai_has_its_own_router_category():
    router = TelegramRouter(
        {},
        {
            "TELEGRAM_THREAD_AI_ID": "777",
            "TELEGRAM_THREAD_NEWS_ID": "1451",
            "TELEGRAM_THREAD_SIGNAL_ID": "9",
        },
    )
    ai = router.resolve("watchlist_ai_interpretation_bbca", "watchlist_ai")
    news = router.resolve("news", "news")
    signal = router.resolve("final_watchlist_detail", "signal")

    assert ai.category == "AI"
    assert ai.message_thread_id == "777"
    assert news.category == "NEWS"
    assert news.message_thread_id == "1451"
    assert signal.category == "SIGNAL"
    assert signal.message_thread_id == "9"


def test_watchlist_ai_formatter_is_narrative_and_compact():
    text = format_watchlist_ai_interpretation({
        "symbol": "BBCA",
        "trade_date": "2026-08-21",
        "decision": "WATCH",
        "analysis": (
            "Menurut saya BBCA masih menarik dipantau karena broker flow tetap mendukung, "
            "namun timing entry belum cukup nyaman untuk agresif."
        ),
        "conclusion": "Saya lebih memilih menunggu konfirmasi daripada mengejar harga.",
    })

    assert "AI VIEW — BBCA" in text
    assert "21 Agustus 2026" in text
    assert "SDE: <b>WATCH</b>" in text
    assert "Menurut saya" in text
    assert "Kesimpulan:" in text
    assert "PT Bank" not in text
    assert "Provider" not in text
    assert "AI Score" not in text


def test_failure_message_says_official_watchlist_is_unchanged():
    text = format_watchlist_ai_failure(
        trade_date="2026-08-21",
        symbols=["BBCA", "ANTM"],
    )
    assert "BBCA, ANTM" in text
    assert "Final Watchlist resmi tetap berhasil" in text
    assert "tidak ada keputusan/level SDE yang diubah" in text
