from __future__ import annotations

import csv

from modules.ai_interpretation import InterpretationResult
from modules.job_runner import enhanced_daily_reports as reports_module
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder


class DeterministicInterpreter:
    def interpret(self, facts: dict, fallback: dict[str, str]) -> InterpretationResult:
        return InterpretationResult(
            main_reason=str(fallback.get("main_reason") or ""),
            main_risk=str(fallback.get("main_risk") or ""),
            execution_note=str(fallback.get("execution_note") or ""),
            source="DETERMINISTIC",
            status="FALLBACK",
        )


def _row(symbol: str, decision: str, score: float) -> dict:
    return {
        "symbol": symbol,
        "decision": decision,
        "confidence": score,
        "technical_score": 80.0,
        "broker_score": 65.0,
        "risk_reward": 2.0,
        "entry_low": 1000,
        "entry_high": 1020,
        "stop_loss": 960,
        "target_1": 1080,
        "target_2": 1120,
    }


def test_final_watchlist_sends_all_actionable_details_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        reports_module,
        "generate_final_watchlist_chart",
        lambda *args, **kwargs: tmp_path / "chart.png",
    )
    builder = EnhancedDailyReportBuilder(
        output_root=tmp_path / "output",
        interpreter=DeterministicInterpreter(),
        max_watchlist_messages=0,
    )

    rows = [
        _row("BOT1", "BUY ON TRIGGER", 99),
        _row("BOT2", "BUY ON TRIGGER", 98),
        _row("BOT3", "BUY ON TRIGGER", 97),
        _row("BOT4", "BUY ON TRIGGER", 96),
        _row("BC1", "BUY CANDIDATE", 95),
        _row("BC2", "BUY CANDIDATE", 94),
        _row("BC3", "BUY CANDIDATE", 93),
        _row("BC4", "BUY CANDIDATE", 92),
        _row("READY1", "BUY CONFIRMED", 100),
        _row("WATCH1", "WATCH", 91),
        _row("WATCH2", "WATCH HIGH", 90),
        _row("AVOID1", "AVOID", 89),
    ]

    artifacts = builder.build_final_watchlist({
        "trade_date": "2026-08-21",
        "rows": rows,
        "provider": "DECISION_ENGINE",
        "source_mode": "ENGINE_OUTPUT",
    })

    details = [item for item in artifacts if item.report_type == "final_watchlist_detail"]
    detail_symbols = [item.symbol for item in details]

    assert len(details) == 8
    assert set(detail_symbols) == {
        "BOT1", "BOT2", "BOT3", "BOT4",
        "BC1", "BC2", "BC3", "BC4",
    }
    assert "READY1" not in detail_symbols
    assert "WATCH1" not in detail_symbols
    assert "WATCH2" not in detail_symbols
    assert "AVOID1" not in detail_symbols

    summary = next(item for item in artifacts if item.report_type == "final_watchlist_summary")
    assert "READY1" not in summary.text
    assert "WATCH1" not in summary.text
    assert "WATCH2" not in summary.text

    csv_artifact = next(item for item in artifacts if item.report_type == "final_watchlist_csv")
    with csv_artifact.attachment_path.open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))

    csv_symbols = {row["symbol"] for row in csv_rows}
    assert "READY1" in csv_symbols
    assert "WATCH1" in csv_symbols
    assert "WATCH2" in csv_symbols
    assert "AVOID1" not in csv_symbols
