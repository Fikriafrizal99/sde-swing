from __future__ import annotations

import csv
from pathlib import Path

from modules.ai_interpretation import GeminiInterpreter
from modules.ai_interpretation.gemini_interpreter import GeminiHTTPError
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder


def _watchlist_row(index: int, decision: str) -> dict:
    return {
        "trade_date": "2026-08-03",
        "generated_at": "2026-08-03T18:37:00+07:00",
        "rank": index,
        "symbol": f"S{index}",
        "decision": decision,
        "confidence": 90 - index,
        "setup": "BREAKOUT_RETEST",
        "trend": "BULLISH",
        "technical_state": "★★★★",
        "technical_quality": 77,
        "entry_readiness": 68,
        "momentum_status": "POSITIVE",
        "rsi": 57,
        "volume_description": "CONFIRMED",
        "volume_ratio_ma20": 1.24,
        "entry_low": 100,
        "entry_high": 105,
        "stop_loss": 95,
        "target_1": 115,
        "target_2": 120,
        "risk_reward": 2.2636250620755885,
        "broker_status": "ACCUMULATION",
        "broker_direction": "ACCUMULATION",
        "broker_confidence": 72,
        "broker_net_flow": 1_500_000_000,
        "broker_buy_ratio": 64,
        "broker_sell_ratio": 32,
        "broker_alignment": "SELARAS",
        "top_buyers": [{
            "broker": "AK",
            "value": 1_000_000_000,
            "avg_price": 103,
            "classification": "Lokal",
        }],
        "top_sellers": [{
            "broker": "TP",
            "value": 500_000_000,
            "avg_price": 104,
            "classification": "Asing",
        }],
        "avg_buyer_price": 102,
        "avg_seller_price": 104,
        "last_price": 103,
        "distance_to_buyer_avg_pct": 0.98,
        "broker_raw_coverage": 96,
        "execution_state": "WAITING — TUNGGU AREA ENTRY / TRIGGER",
        "trigger_description": "Harga masuk area entry dan volume menguat.",
        "waiting_triggers": [
            "Harga masuk area entry",
            "Volume menguat",
            "Broker flow tetap positif",
        ],
        "main_reason_technical": "Trend bullish dan momentum positif.",
        "main_reason_broker": "Broker accumulation mendukung.",
        "main_reason_entry": "Harga masih dekat area entry.",
        "risk_items": [
            "Harga berisiko membuka terlalu tinggi.",
            "Flow broker dapat melemah.",
        ],
        "invalidation": "harga menembus support utama",
        "sector_state": "ROTATING_IN",
        "market_regime": "BULLISH_MODERATE",
        "data_status": "VALID",
        "yahoo_status": "VALID",
        "zapi_status": "MATCH_WITH_TOLERANCE",
        "source": "TEST",
    }


def test_gemini_without_key_uses_deterministic_fallback() -> None:
    interpreter = GeminiInterpreter(
        api_key="",
        model="gemini-test",
        cache_enabled=False,
    )
    result = interpreter.interpret(
        {"symbol": "ANTM", "decision": "BUY"},
        {"main_reason": "Alasan engine", "main_risk": "Risiko engine"},
    )
    assert result.source == "DETERMINISTIC"
    assert result.status == "FALLBACK"
    assert result.main_reason == "Alasan engine"
    assert result.main_risk == "Risiko engine"


def test_final_watchlist_uses_agreed_format_and_exports_active_rows(tmp_path: Path) -> None:
    builder = EnhancedDailyReportBuilder(
        output_root=tmp_path,
        interpreter=GeminiInterpreter(api_key="", cache_enabled=False),
        max_watchlist_messages=5,
    )
    decisions = ["BUY", "BUY_CANDIDATE", "WATCH_HIGH", "WATCH", "WATCH", "WAIT", "AVOID"]
    rows = [_watchlist_row(index, decision) for index, decision in enumerate(decisions, 1)]

    artifacts = builder.build_final_watchlist({
        "trade_date": "2026-08-03",
        "generated_at": "2026-08-03T18:37:00+07:00",
        "provider": "ZAPI IDX + STOCKBIT",
        "source_mode": "LIVE",
        "coverage": 96,
        "market_regime": "BULLISH_MODERATE",
        "execution_mode": "SELECTIVE",
        "rows": rows,
    })
    summary = [item for item in artifacts if item.report_type == "final_watchlist_summary"]
    detail = [item for item in artifacts if item.report_type == "final_watchlist_detail"]
    csv_items = [item for item in artifacts if item.report_type == "final_watchlist_csv"]

    assert len(summary) == 1
    assert "🎯 SDE SWING — FINAL WATCHLIST" in summary[0].text
    assert "🕒 Dibuat: 18:37 WIB" in summary[0].text
    assert "• BUY READY      : 1" in summary[0].text
    assert "• BUY CANDIDATE  : 1" in summary[0].text
    assert "• WATCH          : 3" in summary[0].text

    assert len(detail) == 5
    text = detail[0].text
    required_sections = [
        "<b>📈 SDE SWING — FINAL WATCHLIST</b>",
        "<b>🎯 TRADE SETUP</b>",
        "<b>🏦 BROKER SUMMARY</b>",
        "<b>🟢 Top Buy</b>",
        "<b>🔴 Top Sell</b>",
        "<b>📌 SETUP CONTEXT</b>",
        "<b>Reason:</b>",
    ]
    for section in required_sections:
        assert section in text
    assert "S1 | BREAKOUT RETEST" in text
    assert "💰 103 | Entry 100–105" in text
    assert "🛑 SL 95 | 🎯 TP1/TP2 115 | 120" in text
    assert "1. AK — Rp1,00B | Avg Rp103" in text
    assert "Buy Avg +0.98%" in text
    assert "Yahoo: VALID" not in text
    assert "ZAPI IDX:" not in text

    assert len(csv_items) == 1
    assert "validasi data" not in csv_items[0].caption.lower()
    csv_path = csv_items[0].attachment_path
    assert csv_path is not None and csv_path.exists()
    assert csv_path.name == "sde-final-watchlist-2026-08-03.csv"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == 5
    assert {row["decision"] for row in exported}.isdisjoint({"WAIT", "AVOID"})
    assert {row["symbol"] for row in exported} == {"S1", "S2", "S3", "S4", "S5"}


def test_market_outlook_matches_final_agreed_layout(tmp_path: Path) -> None:
    builder = EnhancedDailyReportBuilder(
        tmp_path,
        GeminiInterpreter(api_key="", cache_enabled=False),
    )
    artifact = builder.build_market_outlook({
        "trade_date": "2026-08-03",
        "market_regime": "BULLISH MODERATE",
        "execution_mode": "SELECTIVE AGGRESSIVE",
        "ihsg_change": 1.65,
        "ihsg_trend": "BULLISH",
        "ihsg_momentum": "POSITIVE",
        "breadth": "POSITIVE",
        "rotating_in": ["Financials", "Basic Materials"],
        "leading": ["Infrastructure"],
        "weakening": ["Technology"],
        "rotating_out": ["Consumer Cyclicals"],
        "provider": "YAHOO",
        "source_mode": "LIVE",
        "coverage": 96,
        "global_market_status": "VALID",
        "global_sentiment": {
            "sentiment_state": "RISK_ON",
            "positive_instruments": ["brent"],
            "negative_instruments": ["dow"],
            "neutral_instruments": [],
            "missing_instruments": [],
        },
        "global_instruments": [
            {
                "key": "brent_crude",
                "display_name": "Brent Crude Oil",
                "symbol": "BZ=F",
                "close": 82.49,
                "change_pct": 3.83,
                "freshness_status": "VALID",
            },
            {
                "key": "dow_jones",
                "display_name": "Dow Jones",
                "symbol": "^DJI",
                "close": 53885.10,
                "change_pct": -0.85,
                "freshness_status": "VALID",
            },
            {
                "key": "usd_idr",
                "display_name": "USD/IDR",
                "symbol": "IDR=X",
                "close": 16280,
                "change_pct": 0.32,
                "freshness_status": "VALID",
            },
        ],
    })
    text = artifact.text
    assert "<b>🌅 SDE SWING — MARKET OUTLOOK</b>" in text
    assert "<b>📊 MARKET CONDITION</b>" in text
    assert "🟢 Regime    : <b>BULLISH MODERATE</b>" in text
    assert "🎯 Execution : <b>SELECTIVE AGGRESSIVE</b>" in text
    assert "🌍 Global    : <b>RISK ON | Coverage 96%</b>" in text
    assert "📈 IHSG      : <b>BULLISH</b>" in text
    assert "⚡ Momentum  : <b>POSITIVE</b>" in text
    assert "📊 Breadth   : <b>POSITIVE</b>" in text
    assert "<b>🌍 GLOBAL MARKET</b>" in text
    assert "Brent Oil" in text and "🟢 <b>+3,83%</b>" in text
    assert "Dow Jones" in text and "🔴 <b>-0,85%</b>" in text
    assert "USD/IDR" in text and "🔴 <b>+0,32%</b>" in text
    assert "<b>📌 INTERPRETASI</b>" in text
    assert "<b>🔄 ROTASI SEKTOR</b>" in text
    assert "<b>🎯 TRADING PLAN</b>" in text
    assert "<b>📌 SDE BIAS BESOK</b>" in text
    assert "<b>📡 DATA STATUS</b>" in text
    assert "Rp" not in text
    assert "data tidak tersedia" not in text.lower()
    assert "ZAPI ENRICHMENT" not in text
    assert "Sentimen global adalah konteks Market Outlook" in text


def test_post_market_matches_final_agreed_sections_and_counts(tmp_path: Path) -> None:
    builder = EnhancedDailyReportBuilder(
        tmp_path,
        GeminiInterpreter(api_key="", cache_enabled=False),
    )
    artifact = builder.build_post_market({
        "trade_date": "2026-08-04",
        "finished_at": "2026-08-04T18:37:00+07:00",
        "process_status": "SUCCESS_WITH_WARNING",
        "market_regime": "STRONG_BULLISH",
        "ihsg_change": 1.69,
        "ihsg_status": "CURRENT_SESSION",
        "ihsg_data_date": "2026-08-04",
        "technical_data_date": "2026-08-04",
        "technical_bullish_count": 350,
        "technical_neutral_count": 50,
        "technical_bearish_count": 37,
        "symbols_requested": 441,
        "symbols_loaded": 439,
        "symbols_valid": 437,
        "symbols_failed": 2,
        "symbols_skipped": 0,
        "coverage": 99.1,
        "technical_status": "READY",
        "candidate_status": "READY",
        "candidate_data_date": "2026-08-04",
        "setup_distribution": {
            "DEVELOPING": 15,
            "BREAKOUT": 9,
            "TREND_CONTINUATION": 8,
            "PULLBACK": 7,
            "EARLY_ACCUMULATION": 1,
        },
        "broker_status": "READY",
        "historical_status": "VALID",
        "zapi_status": "SUCCESS_WITH_WARNING",
        "zapi_note": "Menggunakan completed trading session terakhir.",
        "stockbit_status": "READY",
        "run_id": "POST-20260804-183700",
    })
    text = artifact.text
    ordered_sections = [
        "📊 MARKET PULSE",
        "📈 TECHNICAL BREADTH",
        "🔥 SETUP DISTRIBUTION",
        "🧭 ARAHAN BESOK",
    ]
    positions = [text.index(section) for section in ordered_sections]
    assert positions == sorted(positions)
    assert "🕒 18:37 WIB" in text
    assert "Bullish 80% · Neutral 11% · Bearish 9%" in text
    assert "🟢 IHSG    : +1,69%" in text
    assert "🧭 Market  : RISK-ON" in text
    assert "📊 Breadth : BULLISH DOMINANT" in text
    assert "🟡 Neutral : 50" in text
    assert "• DEVELOPING : 15" in text
    assert "POST-20260804-183700" in text
    for forbidden in (
        "🏦 BROKER STATUS",
        "📦 SYSTEM HEALTH",
        "🎯 NEXT — FINAL WATCHLIST",
        "PROCESS STATUS",
        "SOURCE STATUS",
        "ZAPI",
    ):
        assert forbidden not in text.upper()


def test_post_market_does_not_invent_missing_screening_counts(tmp_path: Path) -> None:
    builder = EnhancedDailyReportBuilder(
        tmp_path,
        GeminiInterpreter(api_key="", cache_enabled=False),
    )
    artifact = builder.build_post_market({
        "trade_date": "2026-08-04",
        "process_status": "SUCCESS",
        "symbols_requested": 10,
        "symbols_loaded": 10,
        "symbols_valid": 10,
        "symbols_skipped": 0,
        "coverage": 100,
    })
    text = artifact.text
    assert "📊 MARKET PULSE" in text
    assert "Data technical sesi berjalan tidak tersedia." in text
    assert "Coverage  : 100,0%" not in text
    assert "🏦 BROKER STATUS" not in text
    assert "📦 SYSTEM HEALTH" not in text
    assert "🎯 NEXT — FINAL WATCHLIST" not in text
    assert "🟢 Bullish" not in text
    assert "BUY READY" not in text
    assert "BUY CANDIDATE" not in text
    assert "ZAPI" not in text.upper()


class CountingInterpreter(GeminiInterpreter):
    def __init__(self, **kwargs) -> None:
        cache_enabled = bool(kwargs.pop("cache_enabled", False))
        super().__init__(
            api_key="test-key",
            model="gemini-test",
            cache_enabled=cache_enabled,
            **kwargs,
        )
        self.requests: list[str] = []

    def _request(self, facts: dict, report_kind: str) -> dict:
        self.requests.append(report_kind)
        return {
            "main_reason": "Konteks mendukung eksekusi selektif.",
            "main_risk": "Volatilitas tetap perlu diwaspadai.",
            "execution_note": "Tunggu konfirmasi yang sudah ditetapkan engine.",
        }


def test_gemini_request_budget_by_report_type(tmp_path: Path) -> None:
    interpreter = CountingInterpreter(max_watchlist_calls=5)
    builder = EnhancedDailyReportBuilder(
        output_root=tmp_path,
        interpreter=interpreter,
        max_watchlist_messages=5,
    )

    builder.build_post_market({
        "trade_date": "2026-08-05",
        "process_status": "SUCCESS",
        "symbols_requested": 10,
        "symbols_loaded": 10,
        "symbols_valid": 10,
        "symbols_skipped": 0,
        "coverage": 100,
    })
    assert interpreter.requests == []

    builder.build_broker_multiday({
        "trade_date": "2026-08-05",
        "rows": [{
            "symbol": "BBCA",
            "state_1d": "ACCUMULATION",
            "state_3d": "ACCUMULATION",
            "state_5d": "NEUTRAL",
        }],
    })
    assert interpreter.requests == []

    market = {
        "trade_date": "2026-08-05",
        "market_regime": "STRONG BULLISH",
        "execution_mode": "SELECTIVE AGGRESSIVE",
        "ihsg_trend": "BULLISH",
        "breadth": "POSITIVE",
    }
    builder.build_market_outlook(market)
    builder.build_market_outlook({**market, "trade_date": "2026-08-06"})
    assert interpreter.requests.count("MARKET_OUTLOOK") == 1

    rows = [_watchlist_row(index, "BUY_CANDIDATE") for index in range(1, 21)]
    rows.extend([
        _watchlist_row(21, "WAIT"),
        _watchlist_row(22, "AVOID"),
    ])
    builder.build_final_watchlist({
        "trade_date": "2026-08-05",
        "rows": rows,
        "market_regime": "STRONG BULLISH",
        "execution_mode": "SELECTIVE AGGRESSIVE",
    })
    assert interpreter.requests.count("FINAL_WATCHLIST") == 5
    assert len(interpreter.requests) == 6


class QuotaInterpreter(GeminiInterpreter):
    def __init__(self) -> None:
        super().__init__(
            api_key="test-key",
            model="gemini-test",
            max_retries=3,
            cache_enabled=False,
        )
        self.request_count = 0

    def _request(self, facts: dict, report_kind: str) -> dict:
        self.request_count += 1
        raise GeminiHTTPError(429, "quota exceeded")


def test_gemini_429_falls_back_without_retry() -> None:
    interpreter = QuotaInterpreter()
    result = interpreter.interpret(
        {
            "trade_date": "2026-08-05",
            "market_regime": "STRONG BULLISH",
            "execution_mode": "SELECTIVE AGGRESSIVE",
            "ihsg_trend": "BULLISH",
        },
        {"main_reason": "Fallback reason", "main_risk": "Fallback risk"},
    )
    assert interpreter.request_count == 1
    assert result.source == "DETERMINISTIC"
    assert result.status == "FALLBACK"
    assert "Gemini HTTP 429" in result.warning


def test_gemini_success_cache_avoids_repeat_request(tmp_path: Path) -> None:
    cache_dir = tmp_path / "ai-cache"
    facts = {
        "trade_date": "2026-08-05",
        "market_regime": "STRONG BULLISH",
        "execution_mode": "SELECTIVE AGGRESSIVE",
        "ihsg_trend": "BULLISH",
    }
    fallback = {"main_reason": "Fallback reason", "main_risk": "Fallback risk"}

    first = CountingInterpreter(cache_enabled=True, cache_dir=cache_dir)
    first_result = first.interpret(facts, fallback)
    assert first_result.source == "GEMINI"
    assert len(first.requests) == 1

    second = CountingInterpreter(cache_enabled=True, cache_dir=cache_dir)
    second_result = second.interpret(facts, fallback)
    assert second.requests == []
    assert second_result.source == "GEMINI_CACHE"
    assert second_result.status == "CACHE_HIT"
