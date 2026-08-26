from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.telegram.daily_report_ui import format_watchlist_detail
from modules.telegram.final_watchlist_chart import generate_final_watchlist_chart
from modules.telegram.final_watchlist_ui import format_watchlist_detail as compatibility_formatter


def _row(*, period_type: str = "3D") -> dict:
    return {
        "symbol": "TINS",
        "decision": "BUY ON TRIGGER",
        "setup": "BREAKOUT",
        "trade_date": "2026-08-07",
        "last_price": 3_860,
        "entry_low": 3_820,
        "entry_high": 3_900,
        "active_stop_loss": 3_640,
        "target_1": 4_070,
        "target_2": 4_190,
        "risk_reward": 2.0,
        "technical_status": "VALID_SETUP",
        "confidence": 82,
        "broker_status": "ACCUMULATION",
        "broker_score": 78,
        "broker_net_flow": 9_000_000,
        "buyer_concentration": 0.61,
        "seller_concentration": 0.31,
        "bandar_buy_cost": 3_804.20,
        "distance_to_buy_cost": 0.74,
        "top_buyers": [{"broker": "PX", "value": 9_000_000, "avg_price": 3_804.20, "classification": "ASING"}],
        "top_sellers": [{"broker": "PS", "value": -2_000_000, "avg_price": 3_866.12, "classification": "DOMESTIK"}],
        "broker_period_type": period_type,
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT" if period_type != "1D" else "STOCKBIT_1D",
        "today_pulse_status": "AVAILABLE" if period_type != "1D" else "NOT_APPLICABLE",
        "today_pulse_net_flow": -2_000_000,
        "today_pulse_direction": "DISTRIBUTION",
        "today_pulse_top_buyers": [{"broker": "TD"}],
        "broker_alignment": "NEGATIVE_DIVERGENCE",
        "buy_days": 4,
        "sell_days": 1,
        "trend": "BULLISH",
        "phase": "WAIT_TRIGGER",
        "support": 3_370,
        "resistance": 3_980,
        "waiting_triggers": ["WAIT_FOR_ENTRY_TRIGGER"],
        "main_reason": "Exact PRIMARY broker dan setup teknikal tetap menjadi fakta SDE.",
    }


def test_detail_uses_locked_compact_primary_facts() -> None:
    text = format_watchlist_detail(_row())

    assert "📈 <b>TINS | BUY ON TRIGGER | 82%</b>" in text
    assert "🏦 ACCUMULATION 78/100" in text
    assert "Net +Rp9M | B/S 4/1" in text
    assert "Cost 3.804 (+0,74%)" in text
    assert "🟢 PX 9M" in text and "🔴 PS 2M" in text
    assert "Tunggu break >3.980. Jangan chase." in text
    assert "BROKER PRIMARY" not in text
    assert "TODAY PULSE" not in text
    assert "NEGATIVE DIVERGENCE" not in text


def test_primary_1d_does_not_add_duplicate_context_to_compact_card() -> None:
    text = format_watchlist_detail(_row(period_type="1D"))

    assert "TODAY PULSE" not in text
    assert "PRIMARY 1D" not in text
    assert "🏦 ACCUMULATION 78/100" in text


def test_compatibility_module_is_an_alias_not_second_formatter() -> None:
    assert compatibility_formatter is format_watchlist_detail


def test_explicit_trigger_wins_without_inferring_resistance() -> None:
    row = _row()
    row.update({
        "trigger_description": "Close di atas 4.000 dengan volume valid",
        "resistance": 3_980,
    })
    text = format_watchlist_detail(row)

    assert "Trigger: Close di atas 4.000 dengan volume valid. Jangan chase." in text
    assert "break >3.980" not in text


def test_chart_still_uses_existing_historical_candles(tmp_path: Path) -> None:
    historical = tmp_path / "historical"
    historical.mkdir()
    dates = pd.date_range("2026-04-01", periods=90, freq="B")
    base = pd.Series(range(90), dtype=float) + 3_200
    pd.DataFrame({
        "Date": dates,
        "Open": base + 1,
        "High": base + 20,
        "Low": base - 20,
        "Close": base + 5,
        "Volume": 1_000_000 + base * 10,
    }).to_csv(historical / "TINS.csv", index=False)

    output = generate_final_watchlist_chart(
        _row(), historical_dir=historical, output_dir=tmp_path / "charts", candle_limit=80
    )
    assert output.name == "TINS_setup.png"
    assert output.exists() and output.stat().st_size > 1_000
