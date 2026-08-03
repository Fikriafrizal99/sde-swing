from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from modules.job_runner.delivery import _attachment_caption, _attachment_path
from modules.job_runner.enhanced_runtime_bridge import _artifact_payload, _coverage
from modules.job_runner.enhanced_daily_reports import DailyReportArtifact


def test_coverage_percentage() -> None:
    assert _coverage(96, 100) == 96.0
    assert _coverage(0, 0) == 0.0


def test_csv_artifact_converts_to_report_payload(tmp_path: Path) -> None:
    csv_path = tmp_path / "final_watchlist_2026-08-03.csv"
    pd.DataFrame([{"symbol": "ANTM"}]).to_csv(csv_path, index=False)
    artifact = DailyReportArtifact(
        report_type="final_watchlist_csv",
        text="",
        attachment_path=csv_path,
        caption="📎 Final Watchlist lengkap terlampir.",
    )
    payload = _artifact_payload(artifact)
    assert _attachment_path(payload) == csv_path
    assert _attachment_caption(payload) == "📎 Final Watchlist lengkap terlampir."


def test_text_artifact_has_no_attachment() -> None:
    artifact = DailyReportArtifact(
        report_type="final_watchlist_detail",
        text="📌 ANTM | BUY",
        symbol="ANTM",
    )
    payload = _artifact_payload(artifact)
    assert _attachment_path(payload) is None
    assert payload.symbol == "ANTM"
