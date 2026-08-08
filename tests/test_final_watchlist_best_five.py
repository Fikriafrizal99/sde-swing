from __future__ import annotations

import csv

from modules.ai_interpretation import InterpretationResult
from modules.job_runner import enhanced_daily_reports as reports_module
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder


class RecordingInterpreter:
    def __init__(self) -> None:
        self.symbols: list[str] = []

    def interpret(self, facts: dict, fallback: dict[str, str]) -> InterpretationResult:
        self.symbols.append(str(facts.get("symbol") or ""))
        return InterpretationResult(
            main_reason=str(fallback.get("main_reason") or ""),
            main_risk=str(fallback.get("main_risk") or ""),
            execution_note=str(fallback.get("execution_note") or ""),
            source="DETERMINISTIC",
            status="FALLBACK",
        )


def test_final_watchlist_sends_best_five_by_canonical_final_score(tmp_path, monkeypatch) -> None:
    interpreter = RecordingInterpreter()
    monkeypatch.setattr(
        reports_module,
        "_fw_generate_chart",
        lambda *args, **kwargs: tmp_path / "chart.png",
    )
    builder = EnhancedDailyReportBuilder(
        output_root=tmp_path / "output",
        interpreter=interpreter,
        max_watchlist_messages=5,
    )

    scores = [55.0, 99.0, 70.0, 98.0, 65.0, 97.0, 96.0]
    rows = [
        {
            "rank": index,
            "symbol": f"S{index}",
            "decision": "BUY CANDIDATE",
            "confidence": score,
            "technical_score": 70.0,
            "broker_score": 60.0,
            "risk_reward": 2.0,
        }
        for index, score in enumerate(scores, start=1)
    ]

    artifacts = builder.build_final_watchlist(
        {
            "trade_date": "2026-08-07",
            "rows": rows,
            "provider": "DECISION_ENGINE",
            "source_mode": "ENGINE_OUTPUT",
        }
    )

    expected = ["S2", "S4", "S6", "S7", "S3"]
    details = [item for item in artifacts if item.report_type == "final_watchlist_detail"]
    assert [item.symbol for item in details] == expected
    assert interpreter.symbols[:5] == expected

    summary = next(item for item in artifacts if item.report_type == "final_watchlist_summary")
    positions = [summary.text.index(symbol) for symbol in expected]
    assert positions == sorted(positions)
    assert "TOP 5 PRIORITAS" in summary.text
    assert "ZAPI ENRICHMENT" not in summary.text

    csv_artifact = next(item for item in artifacts if item.report_type == "final_watchlist_csv")
    with csv_artifact.attachment_path.open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 7
    assert [row["symbol"] for row in csv_rows[:5]] == expected
