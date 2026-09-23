from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from modules.job_runner import enhanced_runtime_bridge
from modules.job_runner.delivery import _attachment_caption, _attachment_path
from modules.job_runner.enhanced_daily_reports import DailyReportArtifact
from modules.job_runner.enhanced_runtime_bridge import _artifact_payload, _coverage
from modules.job_runner.runtime import RunnerContext


def _write_summary(path: Path, *, start: str, end: str, net_flow: float) -> None:
    pd.DataFrame([{
        "EMITEN": "TPIA", "FROM_DATE": start, "TO_DATE": end,
        "NET_FLOW": net_flow, "BROKER_ACCDIST": "ACCUMULATION",
        "BUYER_CONCENTRATION": 0.70, "SELLER_CONCENTRATION": 0.22,
        "AVG_BUYER_PRICE": 2_210, "AVG_SELLER_PRICE": 2_180,
        "TOP_BUYER_1": "SUMMARY_ONLY", "TOP_SELLER_1": "SUMMARY_ONLY",
    }]).to_csv(path, index=False)


def _write_raw(path: Path, *, start: str, end: str, buyer: str, seller: str) -> None:
    pd.DataFrame([
        {
            "SYMBOL": "TPIA", "FROM_DATE": start, "TO_DATE": end,
            "SIDE": "BUY", "RANK": 1, "BROKER_CODE": buyer,
            "BROKER_TYPE": "ASING", "NET_VALUE": 9_000_000,
            "NET_LOT": 40, "GROSS_VALUE": 9_000_000, "GROSS_LOT": 40,
            "FREQUENCY": 3, "AVG_PRICE": 2_210,
        },
        {
            "SYMBOL": "TPIA", "FROM_DATE": start, "TO_DATE": end,
            "SIDE": "SELL", "RANK": 1, "BROKER_CODE": seller,
            "BROKER_TYPE": "DOMESTIK", "NET_VALUE": -2_000_000,
            "NET_LOT": -10, "GROSS_VALUE": 2_000_000, "GROSS_LOT": 10,
            "FREQUENCY": 1, "AVG_PRICE": 2_180,
        },
    ]).to_csv(path, index=False)


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
        caption="Final Watchlist lengkap terlampir.",
    )
    payload = _artifact_payload(artifact)
    assert _attachment_path(payload) == csv_path
    assert _attachment_caption(payload) == "Final Watchlist lengkap terlampir."


def test_text_artifact_has_no_attachment() -> None:
    artifact = DailyReportArtifact(
        report_type="final_watchlist_detail",
        text="TPIA | BUY",
        symbol="TPIA",
    )
    payload = _artifact_payload(artifact)
    assert _attachment_path(payload) is None
    assert payload.symbol == "TPIA"


def test_final_watchlist_uses_exact_primary_and_keeps_v3_unchanged(tmp_path: Path, monkeypatch) -> None:
    decision_dir = tmp_path / "decision"
    exit_dir = tmp_path / "exit"
    manifest_dir = tmp_path / "manifests"
    broker_dir = tmp_path / "broker"
    for path in (decision_dir, exit_dir, manifest_dir, broker_dir):
        path.mkdir(parents=True, exist_ok=True)

    decision_path = decision_dir / "FINAL_DECISION_V3.csv"
    pd.DataFrame([{
        "Symbol": "TPIA",
        "Decision_Status_Final": "WATCH",
        "Final_Score_V3": 82,
        "Setup_Type": "DEVELOPING",
        "Technical_State": "VALID_SETUP",
        "Broker_Confirmation": "ACCUMULATION",
        "Broker_Score": 78,
        "Data_Quality_Status": "VALID",
    }]).to_csv(decision_path, index=False)
    original_decision_bytes = decision_path.read_bytes()
    pd.DataFrame([{
        "Symbol": "TPIA", "Entry_Zone_Low": 2_207.70,
        "Entry_Zone_High": 2_252.30, "Initial_Stop": 2_094.64,
        "Target_1": 2_370, "Target_2": 6_300,
        "RR_To_Resistance": 0.75, "Plan_Status": "NOT READY",
        "Decision_Status_Final": "AVOID",
    }]).to_csv(exit_dir / "ENTRY_PLANS.csv", index=False)
    (manifest_dir / "DECISION_ENGINE_MANIFEST_TEST-CANONICAL.json").write_text(
        json.dumps({"Data_Quality_Status": "VALID", "Decision_Owner": "FINAL_DECISION_ENGINE"}),
        encoding="utf-8",
    )

    primary_summary = broker_dir / "PRIMARY_3D_SUMMARY.csv"
    primary_raw = broker_dir / "PRIMARY_3D_RAW.csv"
    today_summary = broker_dir / "TODAY_1D_SUMMARY.csv"
    today_raw = broker_dir / "TODAY_1D_RAW.csv"
    _write_summary(primary_summary, start="2026-08-05", end="2026-08-07", net_flow=9_000_000)
    _write_raw(primary_raw, start="2026-08-05", end="2026-08-07", buyer="PX", seller="PS")
    _write_summary(today_summary, start="2026-08-07", end="2026-08-07", net_flow=-2_000_000)
    _write_raw(today_raw, start="2026-08-07", end="2026-08-07", buyer="TD", seller="TS")
    canonical_summary = broker_dir / "BROKER_SUMMARY_LATEST.csv"
    canonical_summary.write_text("EMITEN\nTPIA\n", encoding="utf-8")
    canonical_summary.with_suffix(".manifest.json").write_text(json.dumps({
        "snapshot_id": "PRIMARY-3D",
        "broker_period_type": "3D",
        "broker_period_start": "2026-08-05",
        "broker_period_end": "2026-08-07",
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
        "primary_summary_snapshot_path": str(primary_summary),
        "primary_raw_snapshot_path": str(primary_raw),
        "daily_capture_summary_snapshot_path": str(today_summary),
        "daily_capture_raw_snapshot_path": str(today_raw),
        "today_pulse_snapshot_id": "TODAY-1D",
        "today_pulse_source": "STOCKBIT_1D",
    }), encoding="utf-8")
    _write_raw(broker_dir / "BROKER_RAW_LATEST.csv", start="2026-08-07", end="2026-08-07", buyer="WRONG", seller="WRONG")

    ctx = RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 7),
        run_id="TEST-CANONICAL",
        config={"paths": {
            "decision_output_dir": str(decision_dir),
            "exit_output_dir": str(exit_dir),
            "manifest_dir": str(manifest_dir),
            "broker_summary_latest": str(canonical_summary),
        }},
        scheduler_config={},
        calendar_config={},
    )

    captured: dict[str, object] = {}

    class CapturingBuilder:
        def build_final_watchlist(self, data):
            captured.update(data)
            return []

    monkeypatch.setattr(enhanced_runtime_bridge, "validate_final_watchlist_sources", lambda *args, **kwargs: {})
    monkeypatch.setattr(enhanced_runtime_bridge, "_zapi_lineage", lambda _ctx: ({}, {}, []))
    monkeypatch.setattr(enhanced_runtime_bridge, "_builder", lambda _ctx: CapturingBuilder())

    assert enhanced_runtime_bridge.final_watchlist_payloads(ctx, {"Run_ID": "TEST-CANONICAL"}) == []
    row = captured["rows"][0]
    assert row["decision"] == "AVOID"
    assert row["confidence"] == 82
    assert row["broker_score"] == 78
    assert row["broker_net_flow"] == 9_000_000
    assert row["top_buyers"][0]["broker"] == "PX"
    assert row["top_sellers"][0]["broker"] == "PS"
    assert row["today_pulse_net_flow"] == -2_000_000
    assert row["today_pulse_top_buyers"][0]["broker"] == "TD"
    assert row["broker_alignment"] == "NEGATIVE_DIVERGENCE"
    assert row["primary_raw_status"] == "AVAILABLE"
    assert "multi_day_flow" not in row
    assert "flow_persistence" not in row
    assert "buy_days" not in row
    assert "sell_days" not in row
    assert decision_path.read_bytes() == original_decision_bytes
