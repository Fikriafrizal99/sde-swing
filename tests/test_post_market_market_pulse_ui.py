from __future__ import annotations

from modules.telegram.post_market_ui import format_post_market


def test_post_market_is_market_first_and_hides_zapi_diagnostics() -> None:
    text = format_post_market({
        "trade_date": "2026-08-12",
        "finished_at": "2026-08-12T16:33:00+07:00",
        "process_status": "SUCCESS_WITH_WARNING",
        "market_regime": "RISK_ON",
        "execution_mode": "SELECTIVE",
        "ihsg_change": 1.69,
        "breadth": "POSITIVE",
        "symbols_requested": 785,
        "symbols_loaded": 779,
        "symbols_valid": 773,
        "symbols_not_loaded": 6,
        "symbols_invalid": 6,
        "symbols_skipped": 0,
        "coverage": 98.5,
        "data_impact": "TIDAK_MATERIAL",
        "technical_status": "READY",
        "candidate_status": "READY",
        "broker_status": "READY",
        "historical_status": "VALID",
        "zapi_status": "DEGRADED",
        "zapi_note": "completed session fallback",
        "degraded_reason": "companies:SourceRequestInvalid:ZAPI HTTP 400",
        "run_id": "SDE-POST-MARKET-20260812-163019-41ec",
    })

    ordered = [
        "📊 MARKET PULSE",
        "📈 TECHNICAL BREADTH",
        "🧭 ARAHAN BESOK",
        "🏦 BROKER STATUS",
        "📦 SYSTEM HEALTH",
        "🎯 NEXT — FINAL WATCHLIST",
    ]
    positions = [text.index(section) for section in ordered]
    assert positions == sorted(positions)

    assert "IHSG        : <b>+1,69%</b>" in text
    assert "Regime      : <b>RISK ON</b>" in text
    assert "Coverage      : <b>98,5%</b>" in text
    assert "Data issue    : <b>12 saham</b>" in text
    assert "Yahoo         : <b>VALID</b>" in text
    assert "Stockbit      : <b>READY</b>" in text

    assert "ZAPI" not in text.upper()
    assert "HTTP 400" not in text
    assert "PROCESS STATUS" not in text
    assert "SOURCE STATUS" not in text


def test_post_market_uses_explicit_breadth_and_setup_distribution_without_inventing() -> None:
    text = format_post_market({
        "trade_date": "2026-08-12",
        "market_regime": "BULLISH",
        "ihsg_change": 0.75,
        "breadth": "POSITIVE",
        "technical_bullish_count": 63,
        "technical_neutral_count": 24,
        "technical_bearish_count": 13,
        "setup_distribution": {
            "pullback": 41,
            "breakout": 28,
            "trend_continuation": 19,
        },
        "coverage": 99,
    })

    assert "🟢 Bullish <b>63%</b>" in text
    assert "🟡 Neutral <b>24%</b>" in text
    assert "🔴 Bearish <b>13%</b>" in text
    assert "<b>🔥 SETUP DISTRIBUTION</b>" in text
    assert "• PULLBACK: <b>41</b>" in text
    assert "• BREAKOUT: <b>28</b>" in text
    assert "• TREND CONTINUATION: <b>19</b>" in text
