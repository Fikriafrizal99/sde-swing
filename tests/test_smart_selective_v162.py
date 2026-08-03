from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.data_preprocessor.stockbit_preprocessor import parse_number
from modules.broker_fusion.foreign_flow import aggregate_foreign_flow
from modules.decision_engine.smart_selective_v162 import smart_decision
from modules.exit_engine.exit_engine import resistance_levels


def test_stockbit_compact_decimal_comma_is_not_scaled_100x() -> None:
    assert parse_number("65,12B", compact_decimal_comma=True) == pytest.approx(65.12e9)
    assert parse_number("(2,41B)", compact_decimal_comma=True) == pytest.approx(-2.41e9)
    assert parse_number("8,750") == pytest.approx(8750)


def test_foreign_flow_is_separated_from_domestic_flow() -> None:
    raw = pd.DataFrame(
        [
            {"SYMBOL": "ANTM", "BROKER_TYPE": "Asing", "NET_VALUE": 80.0},
            {"SYMBOL": "ANTM", "BROKER_TYPE": "Asing", "NET_VALUE": -20.0},
            {"SYMBOL": "ANTM", "BROKER_TYPE": "Lokal/Pemerintah", "NET_VALUE": -30.0},
            {"SYMBOL": "ANTM", "BROKER_TYPE": "Lokal/Pemerintah", "NET_VALUE": 10.0},
        ]
    )
    row = aggregate_foreign_flow(raw).iloc[0]
    assert row["Foreign_Net_Value"] == pytest.approx(60.0)
    assert row["Domestic_Net_Value"] == pytest.approx(-20.0)
    assert row["Foreign_Direction"] == "POSITIVE"
    assert row["Foreign_Score"] > 50
    assert row["Domestic_Flow_Score"] < 50


def _decision_row(**overrides):
    row = {
        "Setup_Type": "PULLBACK",
        "Technical_Quality_Score": 82,
        "Entry_Readiness_PreScore": 74,
        "Broker_Confidence": 40,
        "Broker_Direction": "NEUTRAL",
        "Broker_Direction_Score": 0,
        "Broker_Concentration_Component": 10,
        "Broker_Pattern_Component": 10,
        "Domestic_Flow_Score": 50,
        "Foreign_Score": 50,
        "Foreign_Confidence": 0,
        "Relative_Rank_Pct": 4,
        "ATR_Extension": 1.0,
        "Turnover_MA_20": 15_000_000_000,
        "Entry_Hard_Blocker": False,
        "Broker_Divergence": False,
    }
    row.update(overrides)
    return row


def test_broker_accumulation_is_not_mandatory_for_trigger_status() -> None:
    result = smart_decision(_decision_row(), liquidity_score=90, liquidity_class="LIQUID", market_regime="BULL")
    assert result["Decision_Status"] == "BUY ON TRIGGER"
    assert "STRONG_BROKER_DISTRIBUTION" not in json.loads(result["Hard_Blockers"])


def test_strong_broker_distribution_remains_a_hard_blocker() -> None:
    result = smart_decision(
        _decision_row(Broker_Direction="DISTRIBUTION", Broker_Confidence=88, Broker_Direction_Score=-75),
        liquidity_score=90,
        liquidity_class="LIQUID",
        market_regime="BULL",
    )
    assert result["Decision_Status"] == "AVOID"
    assert "STRONG_BROKER_DISTRIBUTION" in json.loads(result["Hard_Blockers"])


def test_resistance_levels_are_strictly_above_planned_entry() -> None:
    highs = [96, 97, 99, 98, 97, 101, 99, 98, 104, 99, 98, 103, 100, 99]
    px = pd.DataFrame({"High": highs})
    levels = resistance_levels(px, entry=100.0, lookback=120)
    assert levels
    assert all(level > 100.0 for level in levels)


def _price_frame(symbol: str) -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=90, freq="B")
    close = pd.Series([100 + i * 0.08 for i in range(len(dates))], dtype=float)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 0.25,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": 5_000_000,
            "Symbol": symbol,
        }
    )


def test_exit_output_separates_ready_trigger_and_opens_only_ready(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    price_dir.mkdir()
    for symbol in ("AAAA", "BBBB"):
        _price_frame(symbol).to_csv(price_dir / f"{symbol}.csv", index=False)

    decisions = pd.DataFrame(
        [
            {
                "Symbol": "AAAA", "Decision_V3": "BUY CANDIDATE", "Decision_Status": "BUY ON TRIGGER",
                "Final_Score_V3": 78, "Technical_Quality_Score": 80, "Technical_Score_Final": 80,
                "Entry_Readiness_PreScore": 75, "Broker_Score": 60, "Broker_Confirmation": "NEUTRAL",
                "Broker_Direction": "NEUTRAL", "Broker_Confidence": 45, "Liquidity_Class": "LIQUID",
                "Market_Regime": "BULL", "Setup_Type": "TREND_CONTINUATION", "ATR_Extension": 1.0,
                "Turnover_MA_20": 20_000_000_000,
            },
            {
                "Symbol": "BBBB", "Decision_V3": "BUY CANDIDATE", "Decision_Status": "BUY ON TRIGGER",
                "Final_Score_V3": 78, "Technical_Quality_Score": 80, "Technical_Score_Final": 80,
                "Entry_Readiness_PreScore": 75, "Broker_Score": 60, "Broker_Confirmation": "NEUTRAL",
                "Broker_Direction": "NEUTRAL", "Broker_Confidence": 45, "Liquidity_Class": "LIQUID",
                "Market_Regime": "BEAR", "Setup_Type": "TREND_CONTINUATION", "ATR_Extension": 1.0,
                "Turnover_MA_20": 20_000_000_000,
            },
        ]
    )
    decision_csv = tmp_path / "decisions.csv"
    decisions.to_csv(decision_csv, index=False)
    output_dir = tmp_path / "exit"
    state_file = tmp_path / "ACTIVE_TRADES.csv"

    script = PROJECT_ROOT / "modules" / "exit_engine" / "exit_engine.py"
    subprocess.run(
        [
            sys.executable, str(script), str(decision_csv), str(price_dir),
            "--state-file", str(state_file), "--output-dir", str(output_dir),
            "--run-id", "TEST-V162", "--open-approved",
        ],
        check=True,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    plans = pd.read_csv(output_dir / "ENTRY_PLANS.csv")
    approved = pd.read_csv(output_dir / "APPROVED_ENTRIES.csv")
    conditional = pd.read_csv(output_dir / "CONDITIONAL_ENTRIES.csv")
    active = pd.read_csv(state_file)

    assert dict(zip(plans["Symbol"], plans["Decision_Status_Final"])) == {
        "AAAA": "BUY READY",
        "BBBB": "BUY ON TRIGGER",
    }
    assert approved["Symbol"].tolist() == ["AAAA"]
    assert conditional["Symbol"].tolist() == ["BBBB"]
    assert active["Symbol"].tolist() == ["AAAA"]
    assert active.iloc[0]["Entry_Price"] == pytest.approx(
        plans.loc[plans["Symbol"].eq("AAAA"), "Entry_Reference_Price"].iloc[0]
    )
