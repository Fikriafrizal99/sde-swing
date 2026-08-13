from __future__ import annotations

from modules.telegram.post_market_ui import format_post_market


def _payload(**overrides):
    payload = {
        "trade_date": "2026-08-12",
        "finished_at": "2026-08-12T16:33:00+07:00",
        "market_regime": "STRONG_BULLISH",
        "ihsg_change": 1.69,
        "ihsg_status": "CURRENT_SESSION",
        "ihsg_data_date": "2026-08-12",
        "technical_data_date": "2026-08-12",
        "technical_bullish_count": 438,
        "technical_neutral_count": 100,
        "technical_bearish_count": 10,
        "symbols_valid": 548,
        "candidate_data_date": "2026-08-12",
        "symbols_requested": 785,
        "symbols_loaded": 779,
        "symbols_valid": 773,
        "coverage": 98.5,
        "technical_status": "READY",
        "candidate_status": "READY",
        "screening_result": "READY",
        "broker_status": "READY",
        "sector_rotation_trade_date": "2026-08-12",
        "sector_rotation_status": "VALID",
        "leading": ["ENERGY"],
        "rotating_in": ["BANKING"],
        "setup_distribution": {"PULLBACK": 41, "BREAKOUT": 28, "TREND_CONTINUATION": 19},
        "historical_status": "VALID",
        "zapi_status": "DEGRADED",
        "zapi_note": "completed session fallback",
        "degraded_reason": "companies:SourceRequestInvalid:ZAPI HTTP 400",
        "run_id": "SDE-POST-MARKET-20260812-163019-41ec",
    }
    payload.update(overrides)
    return payload


def test_market_pulse_uses_all_three_classified_buckets_and_exactly_ten_boxes() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=60,
        technical_neutral_count=20,
        technical_bearish_count=20,
        symbols_valid=100,
    ))
    assert "🟩🟩🟩🟩🟩🟩🟨🟨🟥🟥" in text
    assert "Buy 60% · Neutral 20% · Sell 20%" in text
    assert "🟩🟩🟩🟩🟩🟩🟨🟨🟥🟥🟩" not in text


def test_sample_counts_are_not_forced_to_visual_example_percentages() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=314,
        technical_neutral_count=346,
        technical_bearish_count=117,
        symbols_valid=777,
    ))
    assert "Buy 40% · Neutral 45% · Sell 15%" in text
    assert "🟩🟩🟩🟩🟨🟨🟨🟨🟥🟥" in text
    assert "Buy 60% · Neutral 20% · Sell 20%" not in text


def test_breadth_classification_has_neutral_dominant_threshold() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=20,
        technical_neutral_count=50,
        technical_bearish_count=30,
        symbols_valid=100,
    ))
    assert "📊 Breadth : NEUTRAL DOMINANT" in text


def test_broker_ready_and_waiting_statuses_are_factual() -> None:
    ready = format_post_market(_payload(broker_status="READY"))
    waiting = format_post_market(_payload(broker_status="WAITING"))
    assert "🟢 Stockbit : READY" in ready
    assert "Broker siap digunakan sebagai konfirmasi di Final Watchlist." in ready
    assert "🟡 Stockbit : WAITING" in waiting
    assert "Broker siap digunakan sebagai konfirmasi" not in waiting


def test_html_escaping_is_applied_to_dynamic_values() -> None:
    text = format_post_market(_payload(
        leading=["Energy <Core> & Finance"],
        run_id="RUN<&>",
    ))
    assert "Energy &lt;Core&gt; &amp; Finance" in text
    assert "RUN&lt;&amp;&gt;" in text
    assert "Energy <Core> & Finance" not in text


def test_normal_post_market_hides_zapi_diagnostics_and_legacy_sections() -> None:
    text = format_post_market(_payload())
    for forbidden in (
        "ZAPI", "HTTP 400", "PROCESS STATUS", "CANDIDATE FUNNEL", "SOURCE STATUS",
    ):
        assert forbidden not in text.upper()
    assert "📊 MARKET PULSE" in text
    assert "📈 TECHNICAL BREADTH" in text
    assert "🧭 ARAHAN BESOK" in text
    assert "🏦 BROKER STATUS" in text
    assert "📦 SYSTEM HEALTH" in text
    assert "🎯 NEXT — FINAL WATCHLIST" in text
    assert text.rstrip().endswith("SDE-POST-MARKET-20260812-163019-41ec")
