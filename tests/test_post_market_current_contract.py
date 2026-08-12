from __future__ import annotations

import pandas as pd

from modules.job_runner.post_market_live import _candidate_health
from modules.telegram.post_market_ui import format_post_market


def _payload(**overrides):
    payload = {
        "post_market_report_version": "CURRENT_V2",
        "trade_date": "2026-08-12",
        "finished_at": "2026-08-12T16:30:00+07:00",
        "process_status": "SUCCESS",
        "pipeline_status": "READY_FOR_FINAL_WATCHLIST",
        "market_regime": "BULLISH",
        "ihsg_change": 1.25,
        "ihsg_status": "CURRENT_SESSION",
        "ihsg_data_date": "2026-08-12",
        "technical_data_date": "2026-08-12",
        "technical_bullish_count": 80,
        "technical_neutral_count": 10,
        "technical_bearish_count": 10,
        "technical_status": "READY",
        "coverage": 99.0,
        "candidate_funnel": {
            "technical_rows": 100,
            "candidate_rows": 20,
            "pass_rows": 8,
            "ready_rows": 3,
            "developing_rows": 5,
            "avoid_rows": 12,
        },
        "screening_result": "READY",
        "dominant_filter_reason": "PRICE_EXTENDED",
        "setup_distribution": {"BREAKOUT": 4, "PULLBACK": 2},
        "source_status": {
            "IHSG": "CURRENT_SESSION",
            "TECHNICAL": "CURRENT",
            "CANDIDATE_RANKING": "AVAILABLE",
            "BROKER": "WAITING",
        },
        "leading": ["ENERGY"],
        "rotating_in": ["BANKING"],
        "top_screening_watchlist": [
            {"symbol": "BBCA", "setup": "BREAKOUT", "score": 88, "entry_readiness": 82, "data_quality": "VALID"},
            {"symbol": "ANTM", "setup": "PULLBACK", "score": 84, "entry_readiness": 75, "data_quality": "VALID"},
        ],
    }
    payload.update(overrides)
    return payload


def test_current_post_market_is_hierarchical_and_not_final_watchlist() -> None:
    text = format_post_market(_payload())
    sections = [
        "PROCESS STATUS", "MARKET SUMMARY", "SECTOR BIAS", "DATA QUALITY",
        "CANDIDATE FUNNEL", "FILTER DOMINAN", "SCREENING RESULT",
        "PIPELINE STATUS", "SOURCE STATUS", "NEXT PROCESS",
    ]
    positions = [text.index(section) for section in sections]
    assert positions == sorted(positions)
    assert "POST MARKET TOP WATCHLIST (MAX 5)" in text
    assert "Final Watchlist menentukan" in text
    assert "1. <b>BBCA</b>" in text


def test_current_post_market_rejects_stale_ihsg_and_does_not_show_old_change() -> None:
    text = format_post_market(_payload(
        market_regime="DATA_NOT_CURRENT",
        ihsg_change=None,
        ihsg_status="NOT_CURRENT_SESSION",
        ihsg_data_date="2026-08-11",
    ))
    assert "NOT CURRENT (2026-08-11)" in text
    assert "+1,25%" not in text


def test_candidate_health_excludes_avoid_and_keeps_missing_values_uninvented() -> None:
    frame = pd.DataFrame([
        {"Symbol": "BBCA", "Candidate_Status": "PASS", "Technical_Score": 80, "Setup_Type": "BREAKOUT"},
        {"Symbol": "ENRG", "Candidate_Status": "AVOID", "Technical_Score": 99, "Setup_Type": "BREAKOUT"},
        {"Symbol": "TINS", "Candidate_Status": "PASS", "Technical_Score": 70, "Filter_Reason": "NO_ENTRY"},
    ])
    result = _candidate_health(frame)
    assert result["candidate_funnel"]["avoid_rows"] == 1
    assert all(item["symbol"] != "ENRG" for item in result["top_screening_watchlist"])
    assert all(item["price"] is None for item in result["top_screening_watchlist"])
    assert result["screening_result"] == "READY"
