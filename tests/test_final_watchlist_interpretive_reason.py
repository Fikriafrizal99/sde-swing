from __future__ import annotations

from modules.telegram import _daily_report_ui
from modules.telegram.final_watchlist_ui import format_watchlist_detail


def _hrum_row() -> dict:
    return {
        "symbol": "HRUM",
        "setup": "TREND_CONTINUATION",
        "analysis_date": "2026-08-10",
        "last_price": 850,
        "entry_low": 840,
        "entry_high": 860,
        "active_stop_loss": 800,
        "target_1": 915,
        "target_2": 975,
        "risk_reward": 2.0,
        "technical_status": "bullish",
        "confidence": 73,
        "broker_status": "STRONG_ACCUMULATION",
        "broker_score": 64,
        "broker_net_flow": 22_620_000_000,
        "buy_days": 4,
        "sell_days": 0,
        "buyer_concentration": 0.0497,
        "seller_concentration": 0.1377,
        "top_buyers": [
            {"broker": "BK", "value": 3_440_000_000, "avg_price": 850},
            {"broker": "DR", "value": 2_500_000_000, "avg_price": 850},
            {"broker": "AK", "value": 5_260_000_000, "avg_price": 850},
        ],
        "top_sellers": [
            {"broker": "XC", "value": -7_480_000_000, "avg_price": 850},
            {"broker": "BQ", "value": -1_540_000_000, "avg_price": 845},
            {"broker": "CP", "value": -1_180_000_000, "avg_price": 840},
        ],
        "bandar_buy_cost": 860,
        "distance_to_buy_cost": -1.41,
        "trend": "bullish",
        "phase": "WAIT_TRIGGER",
        "support": 720,
        "resistance": 880,
        "fib_status": "ENGINE_NOT_AVAILABLE_V1_7",
        "engine_final_reason": "generic engine reason that must not own presentation",
    }


def test_package_routes_final_watchlist_to_interpretive_formatter():
    assert _daily_report_ui.format_watchlist_detail is format_watchlist_detail


def test_hrum_card_is_compact_and_reason_explains_why_wait():
    text = format_watchlist_detail(_hrum_row())

    assert "💰 850 | Entry 840–860" in text
    assert "🛑 SL 800 | 🎯 TP1/TP2 915 | 975" in text
    assert "💵 Net Flow +Rp22,62B | 📅 Buy/Sell 4/0" in text
    assert "1. BK — Rp3,44B | Avg Rp850" in text
    assert "💰 Buy Cost 860 | Buy Avg -1.41%" in text
    assert "📈 bullish | WAIT TRIGGER" in text
    assert "📊 Pattern" not in text
    assert "Persistence" not in text

    assert "HRUM masih bullish" in text
    assert "masih di area entry 840–860" in text
    assert "Broker mendukung (STRONG ACCUMULATION)" in text
    assert "buying terlihat lebih konsisten daripada selling" in text
    assert "resistance 880 menjadi konfirmasi terdekat" in text
    assert "Tunggu harga menembus dan bertahan di atas 880" in text
    assert "generic engine reason" not in text
    assert len(text) <= 1024


def test_insufficient_broker_is_explained_as_missing_evidence_not_distribution():
    row = _hrum_row()
    row.update({
        "symbol": "MARK",
        "last_price": 1095,
        "entry_low": 1085,
        "entry_high": 1105,
        "active_stop_loss": 1045,
        "target_1": 1170,
        "target_2": 1230,
        "broker_status": "INSUFFICIENT",
        "broker_score": 0,
        "broker_net_flow": 2_540_000_000,
        "buy_days": 1,
        "sell_days": 0,
        "bandar_buy_cost": 1105,
        "distance_to_buy_cost": -0.73,
        "phase": "NOT_READY",
        "support": 950,
        "resistance": 1125,
    })

    text = format_watchlist_detail(row)

    assert "📌 INSUFFICIENT | Score 0/100" in text
    assert "Score 0 pada kondisi INSUFFICIENT berarti data belum cukup, bukan otomatis distribusi" in text
    assert "net flow sementara +Rp2,54B dan Buy/Sell 1/0 baru dibaca sebagai indikasi awal" in text
    assert "Status masih NOT READY; resistance 1.125 menjadi konfirmasi terdekat" in text
    assert "Tunggu harga menembus dan bertahan di atas 1.125" in text
    assert len(text) <= 1024
