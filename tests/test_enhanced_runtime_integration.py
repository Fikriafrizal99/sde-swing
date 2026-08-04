from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from modules.job_runner.delivery import _attachment_caption, _attachment_path
from modules.job_runner.enhanced_daily_reports import DailyReportArtifact
from modules.job_runner.enhanced_runtime_bridge import _artifact_payload, _coverage
from modules.job_runner import enhanced_runtime_bridge
from modules.job_runner.runtime import RunnerContext
from run_sde_job_integrated import _optional_broker_multiday_payloads


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


def test_optional_multiday_report_skips_missing_manifest(tmp_path: Path) -> None:
    ctx = RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 4),
        run_id="TEST-FINAL-WATCHLIST",
        config={
            "paths": {
                "broker_multiday_output_dir": str(tmp_path / "broker_multiday"),
                "reports_root": str(tmp_path / "reports"),
            },
        },
        scheduler_config={
            "paths": {
                "job_status_root": str(tmp_path / "status"),
                "preview_root": str(tmp_path / "previews"),
            },
        },
        calendar_config={},
    )

    assert _optional_broker_multiday_payloads(ctx) == []
    audit = tmp_path / "reports" / "audit" / "2026-08-04.jsonl"
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line]
    assert rows[-1]["report_type"] == "broker_multi_day_manifest"
    assert rows[-1]["status"] == "ERROR"


def test_zapi_lineage_reads_string_reconciliation_path(tmp_path: Path, monkeypatch) -> None:
    snapshot_dir = tmp_path / "snapshots" / "2026-08-04"
    snapshot_dir.mkdir(parents=True)
    reconciliation = tmp_path / "reconciliation.json"
    reconciliation.write_text(json.dumps({"status": "SUCCESS", "rows": [{"symbol": "BBCA"}]}), encoding="utf-8")
    (snapshot_dir / "latest_snapshot.json").write_text(
        json.dumps({"reconciliation": {"json_path": str(reconciliation)}}),
        encoding="utf-8",
    )
    def fake_resolve(value):
        return tmp_path / "snapshots" if str(value) == "data/output/snapshots" else tmp_path / Path(str(value))

    monkeypatch.setattr(enhanced_runtime_bridge, "resolve", fake_resolve)
    ctx = RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 4),
        run_id="TEST-ZAPI-LINEAGE",
        config={},
        scheduler_config={},
        calendar_config={},
    )

    summary, rows, inputs = enhanced_runtime_bridge._zapi_lineage(ctx)
    assert summary["json_path"] == str(reconciliation)
    assert rows["BBCA"]["symbol"] == "BBCA"
    assert str(reconciliation) in inputs
