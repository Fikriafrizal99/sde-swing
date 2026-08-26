from __future__ import annotations

from pathlib import Path

from modules.ai_interpretation import GeminiInterpreter
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder


ROOT = Path(__file__).resolve().parents[1]


def _row(index: int, decision: str) -> dict:
    price = 100 + index
    return {
        "symbol": f"S{index:02d}",
        "decision": decision,
        "confidence": 95 - index,
        "setup": "PULLBACK",
        "trade_date": "2026-08-26",
        "analysis_date": "2026-08-26",
        "last_price": price,
        "entry_low": price - 2,
        "entry_high": price,
        "active_stop_loss": price - 6,
        "stop_loss": price - 6,
        "target_1": price + 8,
        "target_2": price + 12,
        "risk_reward": 1.8,
        "trend": "BULLISH",
        "phase": "WAIT_TRIGGER",
        "support": price - 10,
        "resistance": price + 4,
        "broker_status": "ACCUMULATION",
        "broker_score": 70,
        "broker_net_flow": 1_000_000_000,
        "bandar_buy_cost": price - 1,
        "distance_to_buy_cost": 1.0,
        "top_buyers": [{"broker": "AB", "value": 700_000_000}],
        "top_sellers": [{"broker": "CD", "value": 200_000_000}],
        "exchange_status": "NORMAL",
    }


def test_final_watchlist_actionable_chart_cards_are_capped_at_ten(tmp_path: Path, monkeypatch) -> None:
    def fake_chart(row, *, historical_dir, output_dir, candle_limit):
        output = Path(output_dir) / f"{row['symbol']}_setup.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-chart")
        return output

    monkeypatch.setattr(
        "modules.job_runner.enhanced_daily_reports.generate_final_watchlist_chart",
        fake_chart,
    )

    builder = EnhancedDailyReportBuilder(
        output_root=tmp_path,
        interpreter=GeminiInterpreter(api_key="", cache_enabled=False),
        max_watchlist_messages=0,
    )
    builder.historical_dir = tmp_path / "historical"
    builder.chart_output_root = tmp_path / "charts"

    rows = []
    for index in range(1, 7):
        rows.append(_row(index, "BUY READY"))
    for index in range(7, 13):
        rows.append(_row(index, "BUY CANDIDATE"))
    for index in range(13, 16):
        rows.append(_row(index, "WATCH"))

    artifacts = builder.build_final_watchlist({
        "trade_date": "2026-08-26",
        "generated_at": "2026-08-26T18:30:00+07:00",
        "rows": rows,
    })

    summary = [item for item in artifacts if item.report_type == "final_watchlist_summary"]
    details = [item for item in artifacts if item.report_type == "final_watchlist_detail"]
    csv_items = [item for item in artifacts if item.report_type == "final_watchlist_csv"]

    assert len(summary) == 1
    assert len(details) == 10
    assert len(csv_items) == 1
    assert "Maksimal 10 chart-card" in summary[0].text
    assert any("BUY READY" in item.text for item in details)
    assert any("BUY CANDIDATE" in item.text for item in details)
    assert all("| WATCH |" not in item.text for item in details)
    assert all(item.attachment_path is not None for item in details)
    assert all(Path(item.attachment_path).suffix.lower() == ".png" for item in details)


def test_final_watchlist_menu_uses_preview_locked_snapshot_flow() -> None:
    source = (ROOT / "RUN_FINAL_WATCHLIST.bat").read_text(encoding="utf-8-sig")

    assert "[1] Jalankan Final Watchlist + kirim Telegram" in source
    assert "maks 10 chart-card" in source
    assert "[2] Preview / cek hasil trading terakhir" in source
    assert "[3] Kirim ulang snapshot yang sudah dicek di [2]" in source
    assert (
        "tools\\final_watchlist_snapshot.py --config config\\pipeline.json --scheduler-config config\\scheduler.json "
        "--trade-date !PREVIEW_DATE! --preview-only"
    ) in source
    assert (
        "tools\\final_watchlist_snapshot.py --config config\\pipeline.json --scheduler-config config\\scheduler.json "
        "--trade-date !RESEND_DATE!"
    ) in source
    assert "resend_final_watchlist_recovery.py" not in source


def test_snapshot_tool_is_presentation_only_hash_locked_and_uses_canonical_config() -> None:
    source = (ROOT / "tools/final_watchlist_snapshot.py").read_text(encoding="utf-8")
    runtime = (ROOT / "modules/job_runner/final_watchlist_snapshot.py").read_text(encoding="utf-8")

    assert 'parser.add_argument("--config", default="config/pipeline.json")' in source
    assert "config/config.json" not in source
    assert "NO_ENGINE_RERUN" in source
    assert "NO_AI_RERUN" in source
    assert "PREVIEW_SEND_LOCKED" in source
    assert "MAX_DETAIL_CARDS = 10" in runtime
    assert "snapshot_manifest_sha256" in runtime
    assert "approved_sha256" in runtime
    assert "FINAL_WATCHLIST_SNAPSHOT_CHART_REQUIRED" in runtime
    assert "FINAL_WATCHLIST_PRESENTATION_SNAPSHOT_V1" in runtime
