from __future__ import annotations

from html import unescape

import pandas as pd

from modules.job_runner.post_market_live import _candidate_health
from modules.telegram.post_market_ui import format_post_market


def _sample(**overrides):
    payload = {
        "post_market_report_version": "CURRENT_V2",
        "trade_date": "2026-08-13",
        "finished_at": "2026-08-13T16:31:00+07:00",
        "market_regime": "STRONG_BULLISH",
        "technical_bullish_count": 314,
        "technical_neutral_count": 346,
        "technical_bearish_count": 117,
        "symbols_valid": 777,
        "technical_data_date": "2026-08-13",
        "ihsg_change": -1.13,
        "ihsg_status": "CURRENT_SESSION",
        "ihsg_data_date": "2026-08-13",
        "leading": ["Transportasi & Logistik"],
        "rotating_in": ["Infrastruktur"],
        "sector_rotation_trade_date": "2026-08-13",
        "sector_rotation_status": "VALID",
        "candidate_data_date": "2026-08-13",
        "setup_distribution": {
            "TREND_CONTINUATION": 15,
            "PULLBACK": 14,
            "DEVELOPING": 8,
            "EARLY_ACCUMULATION": 2,
            "BREAKOUT": 1,
        },
        "broker_status": "READY",
        "coverage": 98.5,
        "technical_status": "READY",
        "candidate_status": "READY",
        "screening_result": "READY",
        "run_id": "SDE-POST-MARKET-20260813-162840-bdcb",
    }
    payload.update(overrides)
    return payload


def _visual(text: str) -> str:
    return unescape(text)


def test_current_post_market_matches_market_first_contract() -> None:
    text = _visual(format_post_market(_sample()))
    expected = """🌆 SDE SWING — POST MARKET
📅 Kamis, 13 Agustus 2026
🕒 16:31 WIB
━━━━━━━━━━━━━━━━━━━

📊 MARKET PULSE
🟩🟩🟩🟩🟨🟨🟨🟨🟥🟥
Buy 40% · Neutral 45% · Sell 15%
🔴 IHSG    : -1,13%
🧭 Market  : SELECTIVE
📊 Breadth : MIXED

🔥 Sektor kuat
• Transportasi & Logistik
• Infrastruktur

📈 TECHNICAL BREADTH
✅ Valid   : 777 saham
🟢 Bullish : 314
🟡 Neutral : 346
🔴 Bearish : 117

🔥 SETUP DISTRIBUTION
• TREND CONTINUATION : 15
• PULLBACK : 14
• DEVELOPING : 8
• EARLY ACCUMULATION : 2
• BREAKOUT : 1

🧭 ARAHAN BESOK
Pasar ditutup melemah dengan breadth cenderung netral.
Fokus pada saham dengan setup matang, technical score tinggi,
dan broker confirmation yang mendukung.

Hindari mengejar saham yang sudah terlalu jauh dari area entry.

🏦 BROKER STATUS
🟢 Stockbit : READY
Broker siap digunakan sebagai konfirmasi di Final Watchlist.

📦 SYSTEM HEALTH
🟢 Coverage  : 98,5%
🟢 Technical : READY
🟢 Screening : READY
📅 Data      : 13 Agustus 2026

🎯 NEXT — FINAL WATCHLIST
Final Watchlist akan menentukan kandidat prioritas,
broker confirmation, Entry, SL, TP, serta keputusan final.

📌 Post Market hanya menggambarkan kondisi pasar setelah penutupan.
Keputusan trading tetap ditentukan pada Final Watchlist.

SDE-POST-MARKET-20260813-162840-bdcb"""
    assert text == expected


def test_current_v2_metadata_does_not_restore_retired_ui() -> None:
    text = format_post_market(_sample())
    for retired in (
        "PROCESS STATUS", "MARKET SUMMARY", "SECTOR BIAS", "DATA QUALITY",
        "CANDIDATE FUNNEL", "FILTER DOMINAN", "SCREENING RESULT",
        "PIPELINE STATUS", "SOURCE STATUS", "NEXT PROCESS", "ZAPI",
    ):
        assert retired not in text.upper()
    assert "CURRENT_V2" not in text


def test_stale_ihsg_hides_old_change() -> None:
    text = format_post_market(_sample(
        ihsg_change=-1.13,
        ihsg_status="NOT_CURRENT_SESSION",
        ihsg_data_date="2026-08-12",
    ))
    assert "-1,13%" not in text
    assert "DATA SESI TERKINI TIDAK TERSEDIA" in text
    assert "2026-08-12" in text


def test_stale_technical_blocks_breadth_and_setup_distribution() -> None:
    text = format_post_market(_sample(
        technical_data_date="2026-08-12",
        candidate_data_date="2026-08-13",
    ))
    assert "314" not in text
    assert "TREND CONTINUATION : 15" not in text
    assert "Data technical breadth sesi berjalan tidak tersedia." in text
    assert "Data setup current tidak tersedia." in text


def test_stale_sector_rotation_is_not_rendered_as_current() -> None:
    text = format_post_market(_sample(
        sector_rotation_trade_date="2026-08-12",
    ))
    assert "Transportasi" not in text
    assert "Infrastruktur" not in text
    assert "Data sektor current tidak tersedia." in text


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
