from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from modules.portfolio import broker_history_context as broker_history
from modules.portfolio import position_management_engine as engine
from modules.portfolio import position_management_runtime as runtime


def _feature_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    defaults = {
        "SMA_20": 100.0,
        "SMA_50": 95.0,
        "ATR_14": 3.0,
        "RSI_14": 55.0,
        "Slope_SMA20_10": 1.0,
        "Technical_Regime": "BULLISH",
        "Above_SMA20": 1,
        "Above_SMA50": 1,
        "SMA20_Above_SMA50": 1,
        "MACD_Bullish": 1,
        "Distance_SMA_20_Pct": 1.0,
        "Volume_Ratio_20": 1.2,
    }
    for key, value in defaults.items():
        if key not in frame:
            frame[key] = value
    return frame


def test_buy_date_daily_high_low_are_not_used_before_actual_buy(monkeypatch, tmp_path: Path) -> None:
    history = tmp_path / "TEST.csv"
    pd.DataFrame({"Date": ["2026-08-12"], "Close": [100]}).to_csv(history, index=False)
    features = _feature_frame([
        {"Date": "2026-08-12", "Open": 100, "High": 110, "Low": 90, "Close": 100, "Volume": 1_000},
        {"Date": "2026-08-13", "Open": 100, "High": 104, "Low": 96, "Close": 102, "Volume": 1_100},
    ])
    monkeypatch.setattr(engine, "compute_features", lambda raw, symbol: features.copy())
    snapshot = engine.technical_snapshot(tmp_path, "TEST", "2026-08-12")
    assert snapshot["max_high_since_buy"] == 104
    assert snapshot["min_low_since_buy"] == 96
    assert snapshot["buy_day_range_policy"] == "CLOSE_ONLY_ON_BUY_DATE"


def test_buy_date_close_can_still_confirm_end_of_session_level(monkeypatch, tmp_path: Path) -> None:
    history = tmp_path / "TEST.csv"
    pd.DataFrame({"Date": ["2026-08-12"], "Close": [100]}).to_csv(history, index=False)
    features = _feature_frame([
        {"Date": "2026-08-12", "Open": 98, "High": 110, "Low": 90, "Close": 103, "Volume": 1_000},
    ])
    monkeypatch.setattr(engine, "compute_features", lambda raw, symbol: features.copy())
    snapshot = engine.technical_snapshot(tmp_path, "TEST", "2026-08-12")
    assert snapshot["max_high_since_buy"] == 103
    assert snapshot["min_low_since_buy"] == 103


def test_portfolio_broker_score_is_frozen_by_snapshot(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    broker_history.ensure_schema(conn)
    record = {
        "broker_date": "2026-08-12",
        "created_at": "2026-08-12T18:00:00+07:00",
        "snapshot_id": "SNAP-1D-TEST",
        "capture_hash": "hash",
        "source": "STOCKBIT_1D",
        "payload": {
            "TOTAL_BUY": 1_000_000_000,
            "TOTAL_SELL": 600_000_000,
            "NET_FLOW": 400_000_000,
            "BUYER_CONCENTRATION": 0.70,
            "SELLER_CONCENTRATION": 0.40,
            "BROKER_ACCDIST": "BIG ACC",
            "AVG_ACCDIST": "ACCUMULATION",
            "TOP3_ACCDIST": "ACCUMULATION",
        },
    }
    first = broker_history._score_records(conn, [record], "TEST")[0]

    def changed_formula(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out["Broker_Direction"] = "DISTRIBUTION"
        out["Broker_Score"] = 1.0
        out["Broker_Confidence"] = 1.0
        out["Broker_Direction_Score"] = -99.0
        return out

    monkeypatch.setattr(broker_history, "broker_score_frame", changed_formula)
    second = broker_history._score_records(conn, [record], "TEST")[0]
    assert second["state"] == first["state"]
    assert second["score"] == first["score"]
    assert second["confidence"] == first["confidence"]
    assert second["direction_score"] == first["direction_score"]
    assert second["scoring_version"] == first["scoring_version"]
    conn.close()


def test_active_portfolio_telegram_is_compact_and_attention_only() -> None:
    results = [
        {
            "symbol": "AAAA",
            "buy_price": 100,
            "current_price": 105,
            "pnl_pct": 5,
            "initial_stop_loss": 95,
            "initial_tp1": 110,
            "initial_tp2": 120,
            "milestone": "PRE_TARGET",
            "management_action": "HOLD",
            "interpretation_main_reason": "HOLD_REASON_SHOULD_NOT_BE_DETAILED",
            "interpretation_main_risk": "-",
            "interpretation_execution_note": "-",
        },
        {
            "symbol": "BBBB",
            "buy_price": 200,
            "current_price": 185,
            "pnl_pct": -7.5,
            "initial_stop_loss": 190,
            "initial_tp1": 220,
            "initial_tp2": 240,
            "active_stop_loss": 190,
            "milestone": "PRE_TARGET",
            "management_action": "EXIT",
            "interpretation_main_reason": "Harga berada di bawah active stop.",
            "interpretation_main_risk": "Thesis invalid.",
            "interpretation_execution_note": "Prioritaskan keluar.",
        },
    ]
    text = runtime.telegram_text(results, "2026-08-12")
    assert "<pre>" in text
    assert "NEEDS ATTENTION" in text
    assert "BBBB | EXIT" in text
    assert "HOLD_REASON_SHOULD_NOT_BE_DETAILED" not in text
    # Broker-flow money is explicitly signed; price/position formatting lives
    # in the portfolio table and remains unsigned there.
    assert runtime.fmt_money(1_250) == "+Rp1.25K"
    assert runtime.fmt_money(1_250_000) == "+Rp1.25M"
    assert runtime.fmt_money(1_250_000_000) == "+Rp1.25B"
