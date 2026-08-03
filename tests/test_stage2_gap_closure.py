from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pandas as pd

from modules.decision_engine.moderate_profiles import assess_liquidity
from modules.decision_engine.smart_selective_v162 import smart_decision
from modules.market_calendar.idx_calendar import is_idx_trading_day, validate_market_date
from modules.runtime_config import RuntimeConfigError, validate_config

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


def test_strict_config_requires_full_top40_broker_coverage():
    cfg = load_cfg()
    broken = copy.deepcopy(cfg)
    broken["broker"]["min_coverage"] = 0.8
    try:
        validate_config(broken, strict=True)
    except RuntimeConfigError as exc:
        assert "100_PERCENT" in str(exc)
    else:
        raise AssertionError("coverage below 100% must fail")


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
