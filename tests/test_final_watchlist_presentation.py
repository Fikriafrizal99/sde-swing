from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from modules.job_runner.delivery import _idempotency_key
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder
from modules.job_runner.reports import ReportPayload
from modules.telegram.daily_report_ui import format_watchlist_detail
from modules.telegram.final_watchlist_chart import (
    IDX_SEPARATOR,
    generate_final_watchlist_chart,
    idx_tick_size,
    round_idx_price,
)


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
            {"broker": "XL", "value": 20_440_000_000, "avg_price": 3365, "classification": "PEMERINTAH"},
            {"broker": "CC", "value": 3_700_000_000, "avg_price": 3352, "classification": "PEMERINTAH"},
            {"broker": "YP", "value": 3_990_000_000, "avg_price": 3380, "classification": "ASING"},
        ],
        "top_sellers": [
            {"broker": "AK", "value": 16_140_000_000, "avg_price": 3425, "classification": "ASING"},
            {"broker": "LG", "value": 5_740_000_000, "avg_price": 3410, "classification": "PEMERINTAH"},
            {"broker": "PD", "value": 1_050_000_000, "avg_price": 3398, "classification": "ASING"},
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
    assert "1. XL — Rp20,44 miliar | Avg Rp3.370 | Pemerintah" in text
    assert "2. CC — Rp3,70 miliar | Avg Rp3.350 | Pemerintah" in text
    assert "3. YP — Rp3,99 miliar | Avg Rp3.380 | Asing" in text
    assert "<b>🔴 Top Sell</b>" in text
    assert "1. AK — Rp16,14 miliar | Avg Rp3.430 | Asing" in text
    assert "2. LG — Rp5,74 miliar | Avg Rp3.410 | Pemerintah" in text
    assert "3. PD — Rp1,05 miliar | Avg Rp3.400 | Asing" in text
    assert "📅 Buy/Sell 4/1" in text
    assert "🎯 Concentration B 72.00% | S 51.00%" in text
    assert "💰 Buy Cost 3.370 | Jarak Buy Avg +0.74%" in text
    assert "<b>📌 SETUP CONTEXT</b>" in text
    assert "<b>Reason:</b>" in text
    assert "ENGINE_DATA_NOT_AVAILABLE" not in text


def test_idx_tick_rounding_for_final_watchlist_display():
    assert idx_tick_size(199) == 1
    assert idx_tick_size(200) == 2
    assert idx_tick_size(500) == 5
    assert idx_tick_size(2000) == 10
    assert idx_tick_size(5000) == 25

    assert round_idx_price(3821.40) == 3820
    assert round_idx_price(3898.60) == 3900
    assert round_idx_price(3640.39) == 3640
    assert round_idx_price(3978.33) == 3980
    assert round_idx_price(3877.20) == 3880

    row = full_row()
    row.update({
        "symbol": "TINS",
        "last_price": 3860,
        "entry_low": 3821.40,
        "entry_high": 3898.60,
        "active_stop_loss": 3640.39,
        "target_1": 4070,
        "target_2": 4190,
        "bandar_buy_cost": 3804.20,
        "support": 3370,
        "resistance": 3978.33,
        "top_buyers": [{"broker": "AK", "avg_price": 3877.20}],
        "top_sellers": [{"broker": "LG", "avg_price": 3866.12}],
    })
    text = format_watchlist_detail(row)
    assert "💰 Current 3.860 | Entry 3.820–3.900" in text
    assert "🛑 SL 3.640 | 🎯 TP1 4.070 | 🚀 TP2 4.190" in text
    assert "1. AK — Avg Rp3.880" in text
    assert "1. LG — Avg Rp3.870" in text
    assert "💰 Buy Cost 3.800" in text
    assert "Jarak Buy Avg +0.74%" in text
    assert "🟢 Support 3.370 | 🔴 Resistance 3.980" in text


def test_final_watchlist_separator_is_exactly_twenty_chars_without_indent():
    assert IDX_SEPARATOR == "━━━━━━━━━━━━━━━━━━━━"
    assert len(IDX_SEPARATOR) == 20
    text = format_watchlist_detail(full_row())
    separator_lines = [line for line in text.splitlines() if line and set(line) == {"━"}]
    assert separator_lines
    assert all(line == IDX_SEPARATOR for line in separator_lines)
    assert all(line == line.strip() for line in separator_lines)


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


class DummyInterpreter:
    def interpret(self, data, fallback):
        return SimpleNamespace(
            main_reason=fallback["main_reason"],
            main_risk=fallback["main_risk"],
            execution_note=fallback["execution_note"],
            source="TEST",
            status="SUCCESS",
            warning="",
        )


def test_final_watchlist_keeps_all_rows_in_csv_but_only_top_five_details(tmp_path: Path):
    rows = []
    for index in range(8):
        rows.append({
            "symbol": f"T{index:03d}",
            "decision": "BUY CONFIRMED" if index < 3 else "WATCH",
            "confidence": 90 - index,
            "technical_score": 90 - index,
            "broker_score": 80 - index,
            "entry_readiness": 100 - index,
            "entry_distance_pct": index / 10,
            "risk_reward": 2.0,
            "setup": "BREAKOUT",
            "entry_low": 100 + index,
            "entry_high": 102 + index,
            "stop_loss": 95 + index,
            "target_1": 110 + index,
            "target_2": 120 + index,
            "main_reason": "Valid setup",
            "main_risk": "Invalid jika SL ditembus",
        })

    builder = EnhancedDailyReportBuilder(
        output_root=tmp_path,
        interpreter=DummyInterpreter(),
        max_watchlist_messages=5,
    )
    artifacts = builder.build_final_watchlist({"trade_date": "2026-08-08", "rows": rows})

    details = [item for item in artifacts if item.report_type == "final_watchlist_detail"]
    csv_items = [item for item in artifacts if item.report_type == "final_watchlist_csv"]

    assert len(details) == 5
    assert len(csv_items) == 1
    csv_frame = pd.read_csv(csv_items[0].attachment_path)
    assert len(csv_frame) == 8
