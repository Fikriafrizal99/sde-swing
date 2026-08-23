from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from modules.decision.candidate import CanonicalCandidate, validate_candidate
from modules.runtime.context import RuntimeContext
from modules.runtime.data_source_manager import DataSourceManager
from modules.runtime.jobs import JOB_DEPENDENCIES, validate_dependency_status
from modules.runtime.status import ALLOWED_JOB_STATUSES, StatusWriter, build_status_payload
from modules.runtime.artifacts import write_artifact


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_context_uses_one_manager_and_metadata(tmp_path: Path) -> None:
    context = RuntimeContext.create("post_market", date(2026, 8, 3), root=ROOT, mode="MOCK", run_id="RUN-1")
    assert context.source_manager is context.source_manager
    metadata = context.provider_metadata
    assert metadata["data_source_mode"] != "LIVE"
    assert metadata["mock_used"] is True
    assert metadata["provider_status"]


def test_status_schema_is_complete_and_nonempty_for_data_job(tmp_path: Path) -> None:
    context = RuntimeContext.create("post_market", date(2026, 8, 3), root=tmp_path, mode="MOCK", run_id="RUN-2", config_path=ROOT / "config/pipeline.json", scheduler_config_path=ROOT / "config/scheduler.json", data_sources_path=ROOT / "config/data_sources.json")
    # Keep the real source config available while writing to an isolated root.
    context.data_sources_path = ROOT / "config/data_sources.json"
    payload = build_status_payload(context, "SUCCESS_WITH_WARNING", "POST_MARKET", details={})
    required = {
        "run_id", "job_name", "job_mode", "status", "current_stage", "trade_date",
        "config_version", "data_status", "data_source_mode", "primary_provider",
        "provider_status", "providers_attempted", "fallback_used", "mock_used",
        "source_health", "source_coverage_ratio", "symbols_requested", "symbols_loaded",
        "symbols_valid", "symbols_failed", "symbols_skipped", "snapshot_ids",
        "dependency_status", "warnings", "errors", "telegram_status",
    }
    assert required <= payload.keys()
    assert payload["data_source_mode"]
    assert payload["provider_status"]
    assert payload["status"] in ALLOWED_JOB_STATUSES


def test_status_writer_writes_dated_and_latest(tmp_path: Path) -> None:
    context = RuntimeContext.create("market_outlook", date(2026, 8, 3), root=ROOT, mode="MOCK", run_id="RUN-3")
    writer = StatusWriter(context, root=tmp_path / "data/output/job_status")
    path = writer.write("SUCCESS", "MARKET_OUTLOOK", details={"snapshot_ids": {"market": "M"}})
    assert path.exists()
    latest = json.loads((tmp_path / "data/output/job_status/market_outlook_latest.json").read_text())
    assert latest["snapshot_ids"]["market"] == "M"
    assert latest["content_hash"]


def test_final_watchlist_dependency_requires_fresh_trade_date_and_config() -> None:
    context = RuntimeContext.create("final_watchlist", date(2026, 8, 3), root=ROOT, mode="MOCK", run_id="RUN-4")
    statuses = {
        name: {"status": "SUCCESS", "trade_date": "2026-08-03", "config_version": "1.7.1"}
        for name in JOB_DEPENDENCIES["final_watchlist"]
    }
    assert validate_dependency_status(context, "final_watchlist", statuses)["valid"]
    statuses["post_market"]["trade_date"] = "2026-08-02"
    assert not validate_dependency_status(context, "final_watchlist", statuses)["valid"]


def test_candidate_requires_provenance_and_restricts_final_actions() -> None:
    candidate = CanonicalCandidate(
        symbol="BBCA", trade_date="2026-08-03", final_action="WATCH",
        source_provenance={"provider": "FILE"}, snapshot_ids={"technical": "T-1"},
    )
    assert validate_candidate(candidate) == []
    candidate.final_action = "BUY READY"
    assert "FINAL_ACTION_INVALID:BUY READY" in validate_candidate(candidate)


def test_snapshot_builder_adds_metadata_and_hash(tmp_path: Path) -> None:
    context = RuntimeContext.create("technical_snapshot", date(2026, 8, 3), root=tmp_path, mode="MOCK", run_id="RUN-5", config_path=ROOT / "config/pipeline.json", scheduler_config_path=ROOT / "config/scheduler.json", data_sources_path=ROOT / "config/data_sources.json")
    context.data_sources_path = ROOT / "config/data_sources.json"
    path = write_artifact(context, "technical", "T-1", [], snapshot_id="T-1")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["config_version"] == "1.7.1"
    assert document["content_hash"]
    assert document["payload"] == []
    assert path.parts[-3:] == ("technical", "2026-08-03", "T-1.json")


def test_file_fallback_does_not_require_stockbit_key(tmp_path: Path, monkeypatch) -> None:
    raw = tmp_path / "broker.csv"
    raw.write_text("SYMBOL,BROKER_CODE,SIDE,NET_VALUE,NET_LOT,TO_DATE\nBBCA,YP,BUY,100000,10,2026-08-03\n")
    monkeypatch.delenv("STOCKBIT_API_KEY", raising=False)
    manager = DataSourceManager(ROOT / "config/data_sources.json", root=ROOT, file_roots={"broker_raw": raw}, mode="FILE")
    assert manager.metadata["STOCKBIT"].mode == "FILE"
    records = manager.candidates_for("BrokerFlow", "BBCA", "2026-08-03")
    assert records["STOCKBIT"].symbol == "BBCA"


def test_manager_exposes_explicit_runtime_modes() -> None:
    for mode in ("MOCK", "CACHE", "FALLBACK"):
        manager = DataSourceManager(ROOT / "config/data_sources.json", root=ROOT, mode=mode, force_mock=(mode == "MOCK"))
        metadata = manager.provider_metadata(record_type="DailyBar")
        assert metadata["data_source_mode"] in {mode, "NOT_CONFIGURED"}
        assert metadata["provider_status"]
