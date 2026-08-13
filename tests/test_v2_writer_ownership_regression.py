from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import modules.job_runner.core as core
from modules.broker_fusion.broker_fusion_publisher import (
    ArtifactWriteLock,
    _broker_period_lineage,
    publish_run_scoped_artifact,
)
from modules.data_sources.decision_bridge import CONTEXT_COLUMNS
from modules.job_runner.runtime import RunnerContext
from swing_utils import file_sha256, write_json


ROOT = Path(__file__).resolve().parents[1]


def _ctx(tmp_path: Path, *, run_id: str = "CONTEXT-RUN") -> RunnerContext:
    return RunnerContext(
        job="broker_multi_day",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 13),
        run_id=run_id,
        no_telegram=True,
        debug=True,
        config={
            "paths": {
                "broker_multiday_output_dir": str(tmp_path / "broker_multiday"),
                "broker_fusion_artifact_root": str(tmp_path / "fusion"),
                "manifest_dir": str(tmp_path / "manifests"),
                "broker_summary_engine": str(tmp_path / "input" / "FINAL_DECISION_V2.csv"),
                "decision_source": str(tmp_path / "input" / "FINAL_DECISION_V2.csv"),
            }
        },
        scheduler_config={
            "paths": {
                "preview_root": str(tmp_path / "previews"),
                "job_status_root": str(tmp_path / "status"),
                "state_root": str(tmp_path / "state"),
                "job_log": str(tmp_path / "job.log"),
            }
        },
        calendar_config={"holidays": [], "special_trading_days": []},
        config_provenance={"config_version": "1.7.0-multisource"},
    )


def _write_multiday_sources(ctx: RunnerContext) -> None:
    output_dir = Path(ctx.config["paths"]["broker_multiday_output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "Symbol": "BBCA",
        "Context": "ACCUMULATION",
        "Score": 81.0,
        "Confidence": 88.0,
        "Penalty": 0.0,
        "Blocker": "NO_HARD_BLOCKER",
    }
    pd.DataFrame([row]).to_csv(output_dir / "BROKER_MULTIDAY_SUMMARY.csv", index=False)
    pd.DataFrame([row]).to_csv(output_dir / "BROKER_MULTIDAY_DETAIL.csv", index=False)
    write_json(
        output_dir / "BROKER_MULTIDAY_MANIFEST.json",
        {"run_id": ctx.run_id, "data_quality_status": "VALID"},
    )


def _publish_revision(
    tmp_path: Path,
    canonical: Path,
    *,
    run_id: str,
    revision: str,
    stale_context: str = "",
) -> bytes:
    run_scoped = tmp_path / "publisher" / run_id / "FINAL_DECISION_V2.csv"
    run_scoped.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                "Symbol": "BBCA",
                "Decision_Status_Final": "WATCH",
                "Final_Score_V3": 70.0,
                "Revision": revision,
                "Broker_MultiDay_Context": stale_context,
            }
        ]
    )
    frame.to_csv(run_scoped, index=False, encoding="utf-8-sig")
    lock_path = tmp_path / "state" / "FINAL_DECISION_V2.writer.lock"
    with ArtifactWriteLock(lock_path, run_id=run_id):
        publish_run_scoped_artifact(
            run_scoped_output=run_scoped,
            canonical_output=canonical,
            base_manifest={
                "Run_ID": run_id,
                "technical_source_hash": f"technical-{revision}",
                "broker_source_hash": f"broker-{revision}",
                "broker_raw_source_hash": f"raw-{revision}",
                "broker_date": "2026-08-13",
            },
            run_id=run_id,
            manifest_dir=tmp_path / "manifests",
            lock_path=lock_path,
        )
    return canonical.read_bytes()


def _canonical_manifest(canonical: Path) -> dict:
    return json.loads(
        canonical.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )


def test_active_runtime_has_no_direct_canonical_v2_writer_bypass() -> None:
    config = json.loads((ROOT / "config" / "pipeline.json").read_text(encoding="utf-8"))
    assert config["paths"]["broker_fusion"] == "modules/broker_fusion/broker_fusion_publisher.py"

    core_source = (ROOT / "modules" / "job_runner" / "core.py").read_text(encoding="utf-8")
    assert ".to_csv(" not in core_source
    assert "modules/broker_fusion/broker_fusion.py" not in core_source
    assert 'write_json(decision_source.with_suffix(".manifest.json")' not in core_source

    active_launchers = [
        ROOT / "master_pipeline.py",
        ROOT / "run_sde_job.py",
        ROOT / "run_sde_job_integrated.py",
        *(ROOT / "modules" / "job_runner").glob("*.py"),
        *(ROOT / "maintenance").glob("*.bat"),
    ]
    for path in active_launchers:
        source = path.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
        assert "modules/broker_fusion/broker_fusion.py" not in source, path


def test_concurrent_context_processing_cannot_overwrite_new_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(tmp_path)
    _write_multiday_sources(ctx)
    canonical = Path(ctx.config["paths"]["decision_source"])
    revision_a = _publish_revision(
        tmp_path, canonical, run_id="PUBLISH-A", revision="A"
    )

    context_write_reached = threading.Event()
    allow_context_write = threading.Event()
    real_atomic_csv = core.atomic_csv

    def delayed_context_write(frame, destination, **kwargs):
        context_write_reached.set()
        if not allow_context_write.wait(timeout=10):
            raise TimeoutError("context publication was not released")
        return real_atomic_csv(frame, destination, **kwargs)

    monkeypatch.setattr(core, "atomic_csv", delayed_context_write)
    result: dict = {}
    errors: list[BaseException] = []

    def run_context() -> None:
        try:
            result.update(core.attach_broker_multiday_context(ctx, fusion_path=canonical))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_context)
    worker.start()
    assert context_write_reached.wait(timeout=10)

    revision_b = _publish_revision(
        tmp_path, canonical, run_id="PUBLISH-B", revision="B"
    )
    allow_context_write.set()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    assert revision_a != revision_b
    assert canonical.read_bytes() == revision_b
    assert pd.read_csv(canonical).loc[0, "Revision"] == "B"

    canonical_manifest = _canonical_manifest(canonical)
    assert canonical_manifest["Run_ID"] == "PUBLISH-B"
    assert canonical_manifest["output_hash"] == file_sha256(canonical)
    assert canonical_manifest["canonical_output_hash"] == file_sha256(canonical)

    derived = Path(result["decision_input_path"])
    assert derived != canonical
    assert pd.read_csv(derived).loc[0, "Revision"] == "A"
    assert pd.read_csv(derived).loc[0, "Broker_MultiDay_Context"] == "ACCUMULATION"


def test_context_interruption_never_partially_writes_canonical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(tmp_path, run_id="INTERRUPTED-CONTEXT")
    _write_multiday_sources(ctx)
    canonical = Path(ctx.config["paths"]["decision_source"])
    original = _publish_revision(
        tmp_path, canonical, run_id="PUBLISH-STABLE", revision="STABLE"
    )

    def interrupted_to_csv(self, handle, *args, **kwargs):
        handle.write("partial")
        handle.flush()
        raise OSError("simulated context interruption")

    monkeypatch.setattr(pd.DataFrame, "to_csv", interrupted_to_csv)
    with pytest.raises(OSError, match="simulated context interruption"):
        core.attach_broker_multiday_context(ctx, fusion_path=canonical)

    assert canonical.read_bytes() == original
    assert _canonical_manifest(canonical)["output_hash"] == file_sha256(canonical)
    derived = tmp_path / "fusion" / ctx.run_id / "FINAL_DECISION_V2_CONTEXT.csv"
    assert not derived.exists()
    if derived.parent.exists():
        assert list(derived.parent.glob(".*.tmp")) == []


def test_cleanup_uses_derived_input_and_preserves_canonical(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path, run_id="CLEAR-CONTEXT")
    canonical = Path(ctx.config["paths"]["decision_source"])
    original = _publish_revision(
        tmp_path,
        canonical,
        run_id="PUBLISH-WITH-LEGACY-CONTEXT",
        revision="CURRENT",
        stale_context="STALE_DISTRIBUTION",
    )

    result = core._prepare_context_free_decision_input(
        ctx,
        canonical_path=canonical,
        context_columns=CONTEXT_COLUMNS,
        bridge_metadata={"status": "SKIPPED_NOT_AVAILABLE"},
    )

    assert canonical.read_bytes() == original
    assert pd.read_csv(canonical).loc[0, "Broker_MultiDay_Context"] == "STALE_DISTRIBUTION"
    assert _canonical_manifest(canonical)["output_hash"] == file_sha256(canonical)

    derived = Path(result["decision_input_path"])
    assert derived != canonical
    assert pd.isna(pd.read_csv(derived).loc[0, "Broker_MultiDay_Context"])
    derived_manifest = json.loads(
        derived.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert derived_manifest["output_hash"] == file_sha256(derived)
    assert derived_manifest["canonical_source_hash"] == file_sha256(canonical)


def test_publisher_owns_broker_period_lineage() -> None:
    broker = ROOT / "tests" / "fixtures" / "does-not-need-to-exist.csv"
    assert _broker_period_lineage(broker) == {}


def test_publisher_reads_broker_period_lineage_sidecar(tmp_path: Path) -> None:
    broker = tmp_path / "BROKER_SUMMARY_LATEST.csv"
    broker.write_text("Symbol\nBBCA\n", encoding="utf-8")
    write_json(
        broker.with_suffix(".manifest.json"),
        {
            "snapshot_id": "PERIOD-3D",
            "broker_period_type": "3D",
            "broker_period_start": "2026-08-11",
            "broker_period_end": "2026-08-13",
            "broker_period_source": "INTERNAL_DAILY_ROLLUP",
            "summary_snapshot_hash": "summary-hash",
            "raw_snapshot_hash": "raw-hash",
        },
    )

    assert _broker_period_lineage(broker) == {
        "broker_period_snapshot_id": "PERIOD-3D",
        "broker_period_type": "3D",
        "broker_period_start": "2026-08-11",
        "broker_period_end": "2026-08-13",
        "broker_period_source": "INTERNAL_DAILY_ROLLUP",
        "broker_period_summary_hash": "summary-hash",
        "broker_period_raw_snapshot_hash": "raw-hash",
    }
