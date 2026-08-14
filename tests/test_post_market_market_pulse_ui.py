from __future__ import annotations

import pytest

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
        "broker_artifact_available": True,
        "broker_data_verified": True,
        "broker_data_current": True,
        "broker_data_date": "2026-08-12",
        "broker_upstream_status": "CURRENT",
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
    assert "Bullish 60% · Neutral 20% · Bearish 20%" in text
    assert "Buy 60%" not in text
    assert "Sell 20%" not in text
    assert "🟩🟩🟩🟩🟩🟩🟨🟨🟥🟥🟩" not in text


def test_sample_counts_are_not_forced_to_visual_example_percentages() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=314,
        technical_neutral_count=346,
        technical_bearish_count=117,
        symbols_valid=777,
    ))
    assert "Bullish 40% · Neutral 45% · Bearish 15%" in text
    assert "🟩🟩🟩🟩🟨🟨🟨🟨🟥🟥" in text
    assert "Bullish 60% · Neutral 20% · Bearish 20%" not in text


def test_breadth_classification_has_neutral_dominant_threshold() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=20,
        technical_neutral_count=50,
        technical_bearish_count=30,
        symbols_valid=100,
    ))
    assert "📊 Breadth : NEUTRAL DOMINANT" in text


def _guidance_section(text: str) -> str:
    return text.split("🧭 ARAHAN BESOK", 1)[1].split("🏦 BROKER STATUS", 1)[0]


@pytest.mark.parametrize(
    (
        "ihsg_change", "bullish", "neutral", "bearish", "breadth_label",
        "expected_sentence", "forbidden_guidance",
    ),
    [
        (
            -1.0, 70, 10, 20, "BULLISH DOMINANT",
            "Pasar ditutup melemah, namun breadth masih didominasi saham bullish.",
            ("breadth cenderung netral", "didominasi saham bearish"),
        ),
        (
            -1.0, 20, 60, 20, "NEUTRAL DOMINANT",
            "Pasar ditutup melemah dengan breadth cenderung netral.",
            ("didominasi saham bullish", "didominasi saham bearish"),
        ),
        (
            1.0, 20, 10, 70, "BEARISH DOMINANT",
            "IHSG ditutup menguat, namun breadth pasar masih didominasi saham bearish.",
            ("breadth cenderung netral", "didominasi saham bullish"),
        ),
        (
            1.0, 70, 10, 20, "BULLISH DOMINANT",
            "Pasar ditutup menguat dengan breadth didominasi saham bullish.",
            ("breadth cenderung netral", "didominasi saham bearish"),
        ),
        (
            1.0, 40, 35, 25, "MIXED",
            "Pasar ditutup menguat dengan breadth campuran.",
            ("didominasi", "dominan", "breadth cenderung netral"),
        ),
    ],
)
def test_guidance_uses_the_displayed_breadth_classification(
    ihsg_change: float,
    bullish: int,
    neutral: int,
    bearish: int,
    breadth_label: str,
    expected_sentence: str,
    forbidden_guidance: tuple[str, ...],
) -> None:
    text = format_post_market(_payload(
        ihsg_change=ihsg_change,
        technical_bullish_count=bullish,
        technical_neutral_count=neutral,
        technical_bearish_count=bearish,
        symbols_valid=bullish + neutral + bearish,
    ))
    guidance = _guidance_section(text)
    assert f"📊 Breadth : {breadth_label}" in text
    assert expected_sentence in guidance
    for forbidden in forbidden_guidance:
        assert forbidden not in guidance.lower()


def test_broker_ready_requires_current_verified_artifact() -> None:
    ready = format_post_market(_payload())
    waiting = format_post_market(_payload(
        broker_status="READY",
        broker_data_date="2026-08-11",
        broker_data_current=False,
        broker_data_verified=False,
        broker_upstream_status="STALE",
    ))
    unavailable = format_post_market(_payload(
        broker_status="READY",
        broker_artifact_available=False,
        broker_data_verified=False,
        broker_data_current=False,
        broker_data_date="",
        broker_upstream_status="FILE_NOT_FOUND",
    ))
    failed = format_post_market(_payload(
        broker_status="READY",
        broker_data_verified=False,
        broker_data_current=False,
        broker_upstream_status="FAILED",
    ))
    assert "🟢 Stockbit : READY" in ready
    assert "Broker siap digunakan sebagai konfirmasi di Final Watchlist." in ready
    assert "🟡 Stockbit : WAITING" in waiting
    assert "Data broker belum terverifikasi untuk sesi berjalan." in waiting
    assert "Stockbit : READY" not in waiting
    assert "🔴 Stockbit : NOT READY" in unavailable
    assert "🔴 Stockbit : NOT READY" in failed
    assert "Data broker belum tersedia untuk konfirmasi." in unavailable
    assert "Stockbit : READY" not in unavailable
    assert "Stockbit : READY" not in failed


def test_broker_ready_string_without_verification_fails_closed() -> None:
    text = format_post_market(_payload(
        broker_status="READY",
        broker_artifact_available=True,
        broker_data_verified=False,
        broker_data_current=False,
        broker_data_date="2026-08-12",
    ))
    assert "Stockbit : READY" not in text
    assert "🟡 Stockbit : WAITING" in text


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
