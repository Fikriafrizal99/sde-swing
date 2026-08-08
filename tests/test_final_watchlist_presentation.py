from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from modules.job_runner.delivery import _idempotency_key
from modules.job_runner.reports import ReportPayload
from modules.telegram.daily_report_ui import format_watchlist_detail
from modules.telegram.final_watchlist_chart import generate_final_watchlist_chart


def full_row():
    return {
        "symbol": "ANTM",
        "setup": "BREAKOUT_RETEST",
        "trade_date": "2026-08-08",
        "last_price": 3390,
        "entry_low": 3350,
        "entry_high": 3400,
        "active_stop_loss": 3220,
        "target_1": 3600,
        "target_2": 3850,
        "risk_reward": 2.4,
        "technical_status": "VALID_SETUP",
        "confidence": 84,
        "broker_status": "BROKER_CONFIRM",
        "broker_score": 78,
        "broker_net_flow": 42_600_000_000,
        "buy_days": 4,
        "sell_days": 1,
        "buyer_concentration": 72,
        "seller_concentration": 51,
        "top_buyers": [
            {"broker": "XL", "avg_price": 3365},
            {"broker": "CC", "avg_price": 3352},
            {"broker": "YP", "avg_price": 3380},
        ],
        "top_sellers": [
            {"broker": "AK", "avg_price": 3425},
            {"broker": "LG", "avg_price": 3410},
            {"broker": "PD", "avg_price": 3398},
        ],
        "broker_pattern": "CONFIRMED_ACCUMULATION",
        "bandar_buy_cost": 3365,
        "distance_to_buy_cost": 0.74,
        "multi_day_flow": "ACCUMULATION",
        "flow_persistence": "STABLE_DOMINANCE",
        "trend": "UPTREND",
        "phase": "WAIT_TRIGGER",
        "support": 3300,
        "resistance": 3600,
        "fib_status": "ENGINE_NOT_AVAILABLE_V1_7",
        "engine_final_reason": "Struktur trend valid dan broker mengonfirmasi akumulasi.",
    }


def test_final_watchlist_format_is_exact_and_bold():
    text = format_watchlist_detail(full_row())
    assert text.startswith("<b>📈 SDE SWING — FINAL WATCHLIST</b>\n━━━━━━━━━━━━━━━━━━━━")
    assert "<b>🎯 TRADE SETUP</b>" in text
    assert "<b>🏦 BROKER SUMMARY</b>" in text
    assert "<b>🟢 Top Buy</b>" in text
    assert "XL @ 3.365" in text and "CC @ 3.352" in text and "YP @ 3.380" in text
    assert "<b>🔴 Top Sell</b>" in text
    assert "AK @ 3.425" in text and "LG @ 3.410" in text and "PD @ 3.398" in text
    assert "📅 Buy/Sell 4/1" in text
    assert "🎯 Concentration B 72.00% | S 51.00%" in text
    assert "<b>📌 SETUP CONTEXT</b>" in text
    assert "<b>Reason:</b>" in text


def test_chart_uses_same_historical_candle_directory(tmp_path: Path):
    historical = tmp_path / "historical"
    historical.mkdir()
    dates = pd.date_range("2026-04-01", periods=90, freq="B")
    base = pd.Series(range(90), dtype=float) + 3200
    frame = pd.DataFrame({
        "Date": dates,
        "Open": base + 1,
        "High": base + 20,
        "Low": base - 20,
        "Close": base + 5,
        "Volume": 1_000_000 + base * 10,
    })
    frame.to_csv(historical / "ANTM.csv", index=False)
    out = generate_final_watchlist_chart(full_row(), historical_dir=historical, output_dir=tmp_path / "charts", candle_limit=80)
    assert out.name == "ANTM_setup.png"
    assert out.exists() and out.stat().st_size > 1000


class DummyContext:
    trade_date = date(2026, 8, 8)
    run_id = "run-a"


def test_final_watchlist_idempotency_tracks_material_setup_not_run_id(tmp_path: Path):
    image = tmp_path / "ANTM_setup.png"
    image.write_bytes(b"png")
    first = ReportPayload("final_watchlist_detail", "a.txt", "text one", symbol="ANTM", material_signature="abc123")
    second = ReportPayload("final_watchlist_detail", "b.txt", "text two", symbol="ANTM", material_signature="abc123")
    changed = ReportPayload("final_watchlist_detail", "c.txt", "text two", symbol="ANTM", material_signature="def456")
    for payload in (first, second, changed):
        setattr(payload, "attachment_path", image)
    assert _idempotency_key(DummyContext(), first) == _idempotency_key(DummyContext(), second)
    assert _idempotency_key(DummyContext(), first) != _idempotency_key(DummyContext(), changed)
