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


def test_final_watchlist_uses_v3_confidence_and_one_primary_broker_window(tmp_path: Path, monkeypatch) -> None:
    decision_dir = tmp_path / "decision"
    exit_dir = tmp_path / "exit"
    manifest_dir = tmp_path / "manifests"
    broker_dir = tmp_path / "broker_multiday"
    for path in (decision_dir, exit_dir, manifest_dir, broker_dir):
        path.mkdir(parents=True, exist_ok=True)

    pd.DataFrame([{
        "Symbol": "TPIA",
        "Decision_Status_Final": "WATCH",
        "Confidence": 75,
        "Final_Score_V3": 82,
        "Setup_Type": "DEVELOPING",
        "Technical_State": "★★★",
        "Broker_Confirmation": "ACCUMULATION",
        "Broker_Score": 78,
        "Data_Quality_Status": "VALID",
    }]).to_csv(decision_dir / "FINAL_DECISION_V3.csv", index=False)
    pd.DataFrame([{
        "Symbol": "TPIA",
        "Entry_Zone_Low": 2207.70,
        "Entry_Zone_High": 2252.30,
        "Initial_Stop": 2094.64,
        "Target_1": 2370,
        "Target_2": 6300,
        "RR_To_Resistance": 0.75,
        "Plan_Status": "NOT READY",
    }]).to_csv(exit_dir / "ENTRY_PLANS.csv", index=False)
    (manifest_dir / "DECISION_ENGINE_MANIFEST_TEST-CANONICAL.json").write_text(
        json.dumps({"Data_Quality_Status": "VALID", "Decision_Owner": "FINAL_DECISION_ENGINE"}),
        encoding="utf-8",
    )

    pd.DataFrame([{
        "Symbol": "TPIA",
        "Primary_Window": "1D",
        "Window": "1D",
        "Classification": "DISTRIBUTION",
        "available_sessions": 1,
        "positive_day_ratio": 0.0,
        "negative_day_ratio": 1.0,
        "cumulative_net_value": -4_310_000_000,
        "buyer_concentration": 0.7047,
        "seller_concentration": 0.3888,
        "weighted_broker_buy_cost": 2155.01,
        "distance_to_buy_cost_pct": 3.4798,
    }]).to_csv(broker_dir / "BROKER_WINDOW_COMPARISON.csv", index=False)
    pd.DataFrame([{
        "Symbol": "TPIA",
        "Context": "DISTRIBUTION",
        "Confidence": 78,
    }]).to_csv(broker_dir / "BROKER_MULTIDAY_SUMMARY.csv", index=False)
    pd.DataFrame([{
        "Symbol": "TPIA",
        "Divergence_Label": "NEGATIVE_DIVERGENCE",
        "Seller_Rotation_Status": "STABLE_DOMINANCE",
    }]).to_csv(broker_dir / "BROKER_MULTIDAY_DETAIL.csv", index=False)

    ctx = RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 7),
        run_id="TEST-CANONICAL",
        config={
            "paths": {
                "decision_output_dir": str(decision_dir),
                "exit_output_dir": str(exit_dir),
                "manifest_dir": str(manifest_dir),
                "broker_multiday_output_dir": str(broker_dir),
            },
        },
        scheduler_config={},
        calendar_config={},
    )

    captured: dict = {}

    class CapturingBuilder:
        def build_final_watchlist(self, data):
            captured.update(data)
            return []

    monkeypatch.setattr(enhanced_runtime_bridge, "validate_final_watchlist_sources", lambda *args, **kwargs: {})
    monkeypatch.setattr(enhanced_runtime_bridge, "_zapi_lineage", lambda _ctx: ({}, {}, []))
    monkeypatch.setattr(enhanced_runtime_bridge, "_builder", lambda _ctx: CapturingBuilder())

    assert enhanced_runtime_bridge.final_watchlist_payloads(ctx, {"Run_ID": "TEST-CANONICAL"}) == []
    row = captured["rows"][0]
    assert row["confidence"] == 82
    assert row["technical_state"] == "NOT READY"
    assert row["broker_status"] == "DISTRIBUTION"
    assert row["broker_score"] == 78
    assert float(row["broker_net_flow"]) == -4_310_000_000
    assert row["buy_days"] == 0 and row["sell_days"] == 1
    assert row["broker_pattern"] == "NEGATIVE_DIVERGENCE"
    assert float(row["distance_to_buy_cost"]) == 3.4798
    assert row["multi_day_flow"] == "DISTRIBUTION"
    assert row["flow_persistence"] == "STABLE_DOMINANCE"
