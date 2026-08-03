from __future__ import annotations

import csv
from pathlib import Path

from modules.ai_interpretation import GeminiInterpreter
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder


def test_gemini_without_key_uses_deterministic_fallback() -> None:
    interpreter = GeminiInterpreter(api_key="", model="gemini-test")
    result = interpreter.interpret(
        {"symbol": "ANTM", "decision": "BUY"},
        {"main_reason": "Alasan engine", "main_risk": "Risiko engine"},
    )
    assert result.source == "DETERMINISTIC"
    assert result.status == "FALLBACK"
    assert result.main_reason == "Alasan engine"
    assert result.main_risk == "Risiko engine"


def test_final_watchlist_sends_top_five_and_exports_all_rows(tmp_path: Path) -> None:
    builder = EnhancedDailyReportBuilder(
        output_root=tmp_path,
        interpreter=GeminiInterpreter(api_key=""),
        max_watchlist_messages=5,
    )
    rows = []
    decisions = ["BUY", "BUY_CANDIDATE", "WATCH_HIGH", "WATCH", "WATCH", "WAIT", "AVOID"]
    for index, decision in enumerate(decisions, 1):
        rows.append({
            "trade_date": "2026-08-03",
            "rank": index,
            "symbol": f"S{index}",
            "decision": decision,
            "confidence": 90 - index,
            "setup": "BREAKOUT_RETEST",
            "entry_low": 100,
            "entry_high": 105,
            "stop_loss": 95,
            "target_1": 115,
            "target_2": 120,
            "risk_reward": 2.0,
            "technical_state": "STRONG",
            "broker_state": "ACCUMULATION",
            "sector_state": "ROTATING_IN",
            "market_regime": "BULLISH_MODERATE",
            "data_status": "VALID",
            "source": "TEST",
        })

    artifacts = builder.build_final_watchlist({
        "trade_date": "2026-08-03",
        "provider": "ZAPI IDX + STOCKBIT",
        "source_mode": "LIVE",
        "coverage": 96,
        "rows": rows,
    })
    detail = [item for item in artifacts if item.report_type == "final_watchlist_detail"]
    csv_items = [item for item in artifacts if item.report_type == "final_watchlist_csv"]
    assert len(detail) == 5
    assert len(csv_items) == 1
    assert "📌 S1 | 🟢 BUY" in detail[0].text

    csv_path = csv_items[0].attachment_path
    assert csv_path is not None and csv_path.exists()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == 7
    assert {row["decision"] for row in exported} >= {"WAIT", "AVOID"}


def test_market_outlook_matches_agreed_sections(tmp_path: Path) -> None:
    builder = EnhancedDailyReportBuilder(tmp_path, GeminiInterpreter(api_key=""))
    artifact = builder.build_market_outlook({
        "trade_date": "2026-08-03",
        "regime_icon": "🟢",
        "market_regime": "BULLISH MODERATE",
        "execution_mode": "SELECTIVE",
        "ihsg_change": 1.65,
        "ihsg_trend": "BULLISH",
        "ihsg_momentum": "POSITIVE",
        "breadth": "POSITIVE",
        "rotating_in": ["Financials", "Basic Materials"],
        "leading": ["Infrastructure"],
        "weakening": ["Technology"],
        "rotating_out": ["Consumer Cyclicals"],
        "provider": "ZAPI IDX",
        "source_mode": "LIVE",
        "coverage": 96,
    })
    assert "🌐 SDE MARKET OUTLOOK" in artifact.text
    assert "🔄 ROTASI SEKTOR" in artifact.text
    assert "🎯 FOKUS BESOK" in artifact.text
    assert "⚠️ HINDARI" in artifact.text
    assert "ZAPI IDX | LIVE | Coverage 96,0%" in artifact.text
