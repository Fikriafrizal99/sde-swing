from __future__ import annotations

import inspect
import json
from pathlib import Path

import modules.job_runner.core as core
import run_sde_job_integrated
from modules.broker_fusion.broker_fusion_publisher import _broker_period_lineage
from modules.runtime.jobs import INTEGRATED_JOB_NAMES, JOB_DEPENDENCIES


ROOT = Path(__file__).resolve().parents[1]


def test_final_watchlist_consumes_decision_output_without_context_writer() -> None:
    core_source = (ROOT / "modules" / "job_runner" / "core.py").read_text(encoding="utf-8")
    final_source = inspect.getsource(core.run_final_from_snapshot)

    assert "decision_input = decision_source" in final_source
    assert "attach_broker_multiday_context" not in core_source
    assert "_prepare_context_free_decision_input" not in core_source
    assert "FINAL_DECISION_V2_CONTEXT" not in core_source
    assert "Broker_MultiDay" not in core_source


def test_final_watchlist_runtime_has_no_retired_broker_dependency_or_cli_route() -> None:
    assert "broker_multi_day" not in INTEGRATED_JOB_NAMES
    assert "broker_multi_day" not in JOB_DEPENDENCIES
    assert JOB_DEPENDENCIES["final_watchlist"] == (
        "market_outlook", "post_market", "broker_summary",
    )
    assert "broker_multi_day" not in run_sde_job_integrated.SUPPORTED_JOBS
    assert "broker_multi_day" not in run_sde_job_integrated.ENHANCED_JOBS


def test_publisher_keeps_exact_primary_period_lineage_from_sidecar(tmp_path: Path) -> None:
    broker = tmp_path / "BROKER_SUMMARY_LATEST.csv"
    broker.write_text("Symbol\nBBCA\n", encoding="utf-8")
    broker.with_suffix(".manifest.json").write_text(json.dumps({
        "snapshot_id": "PERIOD-3D",
        "broker_period_type": "3D",
        "broker_period_start": "2026-08-11",
        "broker_period_end": "2026-08-13",
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
        "summary_snapshot_hash": "summary-hash",
        "raw_snapshot_hash": "raw-hash",
    }), encoding="utf-8")

    assert _broker_period_lineage(broker) == {
        "broker_period_snapshot_id": "PERIOD-3D",
        "broker_period_type": "3D",
        "broker_period_start": "2026-08-11",
        "broker_period_end": "2026-08-13",
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
        "broker_period_summary_hash": "summary-hash",
        "broker_period_raw_snapshot_hash": "raw-hash",
    }
