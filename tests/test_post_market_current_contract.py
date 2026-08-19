from __future__ import annotations

import json
from datetime import date
from html import unescape
from pathlib import Path

import pandas as pd

from modules.job_runner.post_market_live import (
    _broker_presentation_context,
    _candidate_health,
)
from modules.job_runner.runtime import RunnerContext
from modules.telegram.post_market_ui import format_post_market
from swing_utils import file_sha256


def _sample(**overrides):
    payload = {
        "post_market_report_version": "CURRENT_V2",
        "trade_date": "2026-08-18",
        "finished_at": "2026-08-18T16:39:00+07:00",
        "market_regime": "STRONG_BULLISH",
        "technical_bullish_count": 313,
        "technical_neutral_count": 353,
        "technical_bearish_count": 115,
        "symbols_valid": 777,
        "technical_data_date": "2026-08-18",
        "ihsg_change": 0.75,
        "ihsg_status": "CURRENT_SESSION",
        "ihsg_data_date": "2026-08-18",
        "leading": [],
        "rotating_in": [],
        "sector_rotation_trade_date": "",
        "sector_rotation_status": "NOT_CURRENT",
        "candidate_data_date": "2026-08-18",
        "setup_distribution": {
            "DEVELOPING": 15,
            "BREAKOUT": 9,
            "TREND_CONTINUATION": 8,
            "PULLBACK": 7,
            "EARLY_ACCUMULATION": 1,
        },
        "broker_status": "WAITING",
        "broker_artifact_available": True,
        "broker_data_verified": False,
        "broker_data_current": False,
        "broker_data_date": "2026-08-17",
        "broker_upstream_status": "STALE",
        "coverage": 99.0,
        "technical_status": "READY",
        "candidate_status": "READY",
        "screening_result": "READY",
        "run_id": "SDE-POST-MARKET-20260818-163023-f69d",
    }
    payload.update(overrides)
    return payload


def _visual(text: str) -> str:
    return unescape(text)


def test_current_post_market_matches_approved_user_example_1_to_1() -> None:
    text = _visual(format_post_market(_sample()))
    expected = """🌆 SDE SWING — POST MARKET
📅 Selasa, 18 Agustus 2026
🕒 16:39 WIB
━━━━━━━━━━━━━━━━━━━

📊 MARKET PULSE
🟩🟩🟩🟩🟨🟨🟨🟨🟨🟥
Bullish 40% · Neutral 45% · Bearish 15%
🟢 IHSG    : +0,75%
🧭 Market  : SELECTIVE
📊 Breadth : MIXED

🔥 Sektor kuat
⚠️ Data sektor current tidak tersedia.

📈 TECHNICAL BREADTH
✅ Valid   : 777 saham
🟢 Bullish : 313
🟡 Neutral : 353
🔴 Bearish : 115

🔥 SETUP DISTRIBUTION
• DEVELOPING : 15
• BREAKOUT : 9
• TREND CONTINUATION : 8
• PULLBACK : 7
• EARLY ACCUMULATION : 1

🧭 ARAHAN BESOK
Pasar ditutup menguat dengan breadth campuran.
Fokus pada saham dengan setup matang dan konfirmasi yang lengkap.
Tunggu data broker sesi berjalan sebelum menggunakannya sebagai konfirmasi.

Hindari mengejar saham yang sudah terlalu jauh dari area entry.

SDE-POST-MARKET-20260818-163023-f69d"""
    assert text == expected


def test_legacy_and_removed_sections_never_reappear() -> None:
    text = format_post_market(_sample())
    for retired in (
        "PROCESS STATUS",
        "MARKET SUMMARY",
        "SECTOR BIAS",
        "DATA QUALITY",
        "CANDIDATE FUNNEL",
        "FILTER DOMINAN",
        "SCREENING RESULT",
        "PIPELINE STATUS",
        "SOURCE STATUS",
        "NEXT PROCESS",
        "BROKER STATUS",
        "SYSTEM HEALTH",
        "NEXT — FINAL WATCHLIST",
        "POST MARKET HANYA MENGGAMBARKAN",
        "ZAPI",
    ):
        assert retired not in text.upper()
    assert "CURRENT_V2" not in text


def test_stale_ihsg_hides_old_change() -> None:
    text = format_post_market(_sample(
        ihsg_change=-1.13,
        ihsg_status="NOT_CURRENT_SESSION",
        ihsg_data_date="2026-08-17",
    ))
    assert "-1,13%" not in text
    assert "DATA SESI TERKINI TIDAK TERSEDIA" in text
    assert "2026-08-17" in text
    assert "Market  : SELECTIVE" in text


def test_stale_technical_blocks_breadth_and_setup_distribution() -> None:
    text = format_post_market(_sample(
        technical_data_date="2026-08-17",
        candidate_data_date="2026-08-18",
    ))
    assert "313" not in text
    assert "DEVELOPING : 15" not in text
    assert "Data technical breadth sesi berjalan tidak tersedia." in text
    assert "Data setup current tidak tersedia." in text


def test_stale_sector_rotation_is_not_rendered_as_current() -> None:
    text = format_post_market(_sample(
        leading=["Transportasi & Logistik"],
        rotating_in=["Infrastruktur"],
        sector_rotation_trade_date="2026-08-17",
        sector_rotation_status="VALID",
    ))
    assert "Transportasi" not in text
    assert "Infrastruktur" not in text
    assert "Data sektor current tidak tersedia." in text


def test_stale_candidate_hides_setup_distribution() -> None:
    text = format_post_market(_sample(candidate_data_date="2026-08-17"))
    assert "DEVELOPING : 15" not in text
    assert "Data setup current tidak tersedia." in text


def test_ready_broker_only_removes_waiting_guidance_without_adding_section() -> None:
    text = format_post_market(_sample(
        broker_status="READY",
        broker_artifact_available=True,
        broker_data_verified=True,
        broker_data_current=True,
        broker_data_date="2026-08-18",
        broker_upstream_status="CURRENT",
    ))
    assert "Tunggu data broker sesi berjalan" not in text
    assert "BROKER STATUS" not in text


def _ctx(tmp_path: Path, broker_path: Path) -> RunnerContext:
    return RunnerContext(
        job="post_market",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 18),
        run_id="POST-MARKET-BROKER-CONTEXT",
        no_telegram=True,
        debug=True,
        config={"paths": {"broker_summary_latest": str(broker_path)}},
        scheduler_config={
            "paths": {
                "preview_root": str(tmp_path / "previews"),
                "job_status_root": str(tmp_path / "status"),
                "state_root": str(tmp_path / "state"),
                "job_log": str(tmp_path / "job.log"),
            }
        },
        calendar_config={"holidays": [], "special_trading_days": []},
        config_provenance={"config_version": "1.7.1"},
    )


def _write_broker_artifact(
    path: Path,
    *,
    broker_date: str = "2026-08-18",
    upstream_status: str | None = "CURRENT",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "EMITEN": "BBCA",
        "TO_DATE": broker_date,
        "TOTAL_BUY": 1_000_000,
        "TOTAL_SELL": 500_000,
    }]).to_csv(path, index=False)
    if upstream_status is not None:
        sidecar = {
            "status": upstream_status,
            "broker_date": broker_date,
            "summary_hash": file_sha256(path),
        }
        path.with_suffix(".manifest.json").write_text(json.dumps(sidecar), encoding="utf-8")


def test_broker_context_current_valid_artifact_is_ready(tmp_path: Path) -> None:
    broker = tmp_path / "broker" / "BROKER_SUMMARY_LATEST.csv"
    _write_broker_artifact(broker)
    result = _broker_presentation_context(_ctx(tmp_path, broker))
    assert result["broker_status"] == "READY"
    assert result["broker_artifact_available"] is True
    assert result["broker_data_verified"] is True
    assert result["broker_data_current"] is True
    assert result["broker_data_date"] == "2026-08-18"


def test_broker_context_stale_artifact_waits(tmp_path: Path) -> None:
    broker = tmp_path / "broker" / "BROKER_SUMMARY_LATEST.csv"
    _write_broker_artifact(broker, broker_date="2026-08-17")
    result = _broker_presentation_context(_ctx(tmp_path, broker))
    assert result["broker_status"] == "WAITING"
    assert result["broker_data_current"] is False


def test_broker_context_failed_upstream_is_not_ready(tmp_path: Path) -> None:
    broker = tmp_path / "broker" / "BROKER_SUMMARY_LATEST.csv"
    _write_broker_artifact(broker, upstream_status="FAILED")
    result = _broker_presentation_context(_ctx(tmp_path, broker))
    assert result["broker_status"] == "NOT_READY"
    assert result["broker_data_verified"] is False


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
