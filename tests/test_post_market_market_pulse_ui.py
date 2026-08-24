from __future__ import annotations

import pytest

from modules.telegram.post_market_ui import format_post_market


def _daily_sector() -> dict:
    return {
        "status": "VALID",
        "trade_date": "2026-08-12",
        "data_date": "2026-08-12",
        "sectors": [
            {"sector": "ENERGY", "rank": 1, "median_return_1d": 1.50, "positive_breadth": 0.70},
            {"sector": "BASIC", "rank": 2, "median_return_1d": 1.10, "positive_breadth": 0.65},
            {"sector": "TECH", "rank": 3, "median_return_1d": 0.70, "positive_breadth": 0.60},
            {"sector": "INFRA", "rank": 4, "median_return_1d": -0.20, "positive_breadth": 0.45},
            {"sector": "HEALTH", "rank": 5, "median_return_1d": -0.60, "positive_breadth": 0.35},
            {"sector": "FINANCE", "rank": 6, "median_return_1d": -1.00, "positive_breadth": 0.30},
        ],
    }


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
        "daily_sector_trade_date": "2026-08-12",
        "daily_sector_status": "VALID",
        "daily_sector": _daily_sector(),
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


def test_sample_counts_are_not_forced_to_visual_example_percentages() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=314,
        technical_neutral_count=346,
        technical_bearish_count=117,
        symbols_valid=777,
    ))
    assert "Bullish 40% · Neutral 45% · Bearish 15%" in text
    assert "🟩🟩🟩🟩🟨🟨🟨🟨🟥🟥" in text


def test_user_sample_counts_allocate_four_green_five_yellow_one_red() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=313,
        technical_neutral_count=353,
        technical_bearish_count=115,
        symbols_valid=777,
    ))
    assert "Bullish 40% · Neutral 45% · Bearish 15%" in text
    assert "🟩🟩🟩🟩🟨🟨🟨🟨🟨🟥" in text


def test_breadth_classification_has_neutral_dominant_threshold() -> None:
    text = format_post_market(_payload(
        technical_bullish_count=20,
        technical_neutral_count=50,
        technical_bearish_count=30,
        symbols_valid=100,
    ))
    assert "📊 Breadth : NEUTRAL DOMINANT" in text


def test_daily_sector_block_uses_current_session_rows_only() -> None:
    text = format_post_market(_payload())
    assert "🔄 ROTASI SEKTOR HARI INI" in text
    assert "🔥 ENERGY  +1,50% · Breadth 70%" in text
    assert "🔻 FINANCE  -1,00% · Breadth 30%" in text
    assert "LEADING" not in text
    assert "ROTATING IN" not in text

    stale = format_post_market(_payload(
        daily_sector_trade_date="2026-08-11",
        daily_sector_status="VALID",
        daily_sector={**_daily_sector(), "trade_date": "2026-08-11", "data_date": "2026-08-11"},
    ))
    assert "🔥 ENERGY" not in stale
    assert "Data sektor sesi berjalan belum cukup." in stale


def _guidance_section(text: str) -> str:
    section = text.split("🧭 ARAHAN BESOK", 1)[1]
    run_id = "SDE-POST-MARKET-"
    return section.split(run_id, 1)[0] if run_id in section else section


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


def test_broker_ready_changes_guidance_only_not_report_shape() -> None:
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
    assert "Tunggu data broker sesi berjalan" not in ready
    assert "Tunggu data broker sesi berjalan" in waiting
    assert "Tunggu data broker sesi berjalan" in unavailable
    for text in (ready, waiting, unavailable):
        assert "BROKER STATUS" not in text
        assert "SYSTEM HEALTH" not in text
        assert "NEXT — FINAL WATCHLIST" not in text


def test_html_escaping_is_applied_to_dynamic_values() -> None:
    daily = _daily_sector()
    daily["sectors"][0] = {
        "sector": "Energy <Core> & Finance",
        "rank": 1,
        "median_return_1d": 1.5,
        "positive_breadth": 0.7,
    }
    text = format_post_market(_payload(
        daily_sector=daily,
        run_id="RUN<&>",
    ))
    assert "Energy &lt;Core&gt; &amp; Finance" in text
    assert "RUN&lt;&amp;&gt;" in text
    assert "Energy <Core> & Finance" not in text


def test_normal_post_market_contains_only_approved_sections() -> None:
    text = format_post_market(_payload())
    for forbidden in (
        "ZAPI",
        "HTTP 400",
        "PROCESS STATUS",
        "CANDIDATE FUNNEL",
        "SOURCE STATUS",
        "BROKER STATUS",
        "SYSTEM HEALTH",
        "NEXT — FINAL WATCHLIST",
    ):
        assert forbidden not in text.upper()
    assert "📊 MARKET PULSE" in text
    assert "🔄 ROTASI SEKTOR HARI INI" in text
    assert "📈 TECHNICAL BREADTH" in text
    assert "🔥 SETUP DISTRIBUTION" in text
    assert "🧭 ARAHAN BESOK" in text
    assert text.rstrip().endswith("SDE-POST-MARKET-20260812-163019-41ec")
