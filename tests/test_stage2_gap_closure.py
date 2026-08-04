from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from modules.broker_bridge.wait_for_broker_export import inspect as inspect_broker_export
from modules.decision_engine.moderate_profiles import assess_liquidity
from modules.decision_engine.smart_selective_v162 import smart_decision
from modules.market_calendar.idx_calendar import is_idx_trading_day, validate_market_date
from modules.runtime_config import validate_config

ROOT = Path(__file__).resolve().parents[1]


def load_cfg():
    return json.loads((ROOT / "config/pipeline.json").read_text(encoding="utf-8"))


def test_idx_calendar_holiday_and_weekend():
    cfg = load_cfg()
    holidays = cfg["data_freshness"]["market_holidays"]
    assert not is_idx_trading_day("2026-08-17", holidays)
    assert not is_idx_trading_day("2026-08-02", holidays)
    assert is_idx_trading_day("2026-08-03", holidays)
    assert validate_market_date("2026-08-03", holidays=holidays) == date(2026, 8, 3)


def test_operational_broker_policy_replaces_old_full_coverage_gate():
    cfg = load_cfg()
    warnings = validate_config(cfg, strict=True)
    broker = cfg["broker"]

    assert broker["min_coverage"] == 0.8
    assert broker["allow_partial_broker"] is True
    assert broker["required_matched_count"] == 32
    assert broker["ideal_coverage"] == 1.0
    assert broker["coverage_policy"] == "OPERATIONAL_PARTIAL_ALLOWED"
    assert "BROKER_PARTIAL_COVERAGE_ALLOWED:32/40:80%" in warnings


def test_broker_export_39_of_40_is_operationally_ready(tmp_path: Path):
    expected = [f"S{index:03d}" for index in range(40)]
    rows = [
        {
            "FROM_DATE": "2026-07-30",
            "TO_DATE": "2026-08-03",
            "EMITEN": symbol,
            "TOTAL_BUY": 1_000_000,
            "TOTAL_SELL": 900_000,
            "NET_FLOW": 100_000,
            "TOP_BUYER_1": "YP",
            "TOP_SELLER_1": "CC",
            "BUYER_CONCENTRATION": 0.25,
            "SELLER_CONCENTRATION": 0.20,
        }
        for symbol in expected[:39]
    ]
    path = tmp_path / "BROKER_SUMMARY_COMBINED_2026-08-03.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    ready, message, info = inspect_broker_export(path, expected, min_coverage=0.8)

    assert ready is True
    assert message == "OK"
    assert info["matched"] == 39
    assert info["expected"] == 40
    assert info["coverage"] == 39 / 40
    assert info["missing_symbols"] == [expected[-1]]


def test_missing_microstructure_is_not_normal():
    result = assess_liquidity({}, liquidity_score=70, reference_capital=10_000_000, minimum_required_metrics=2)
    assert result["classification"] == "INSUFFICIENT_MICROSTRUCTURE_DATA"
    assert result["position_size_multiplier"] <= 0.35


def test_capital_changes_market_participation_and_classification():
    row = {"Turnover_MA_20": 2_000_000_000, "Turnover_Value": 2_000_000_000, "Frequency": 2000, "Spread_Pct": 0.2, "Depth_Value": 100_000_000}
    small = assess_liquidity(row, liquidity_score=70, reference_capital=10_000_000, max_position_pct=0.2)
    large = assess_liquidity(row, liquidity_score=70, reference_capital=1_000_000_000, max_position_pct=0.2)
    assert large["market_participation"] > small["market_participation"]
    assert large["classification"] in {"THIN_BUT_TRADEABLE", "VERY_POOR"}


def test_missing_microstructure_cannot_be_buy_ready_preplan():
    cfg = load_cfg()["decision"]
    row = {
        "Setup_Type": "BREAKOUT",
        "Technical_Quality_Score": 82,
        "Entry_Readiness_PreScore": 80,
        "Relative_Rank_Pct": 5,
        "Broker_Direction": "ACCUMULATION",
        "Broker_Confirmation": "STRONG ACCUMULATION",
        "Broker_Confidence": 80,
        "Broker_Direction_Score": 80,
        "Foreign_Score": 60,
        "Foreign_Confidence": 70,
        "Foreign_Direction": "POSITIVE",
        "ATR_Extension": 1.0,
        "Data_Quality_Status": "VALID",
    }
    result = smart_decision(pd.Series(row), 75, "NORMAL", "BULL", policy=cfg)
    assert result["Decision_Status_Final"] == "BUY ON TRIGGER"
    assert result["Liquidity_Execution_Class"] == "INSUFFICIENT_MICROSTRUCTURE_DATA"
    assert "MICROSTRUCTURE_CONFIRMATION_REQUIRED" in result["Execution_Conditions"]
