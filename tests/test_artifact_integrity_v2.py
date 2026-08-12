from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.broker_fusion.broker_fusion_publisher import (
    ArtifactWriteLock,
    publish_run_scoped_artifact,
)
from swing_utils import file_sha256, write_json


ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_routes_v2_through_integrity_publisher():
    config = json.loads((ROOT / "config/pipeline.json").read_text(encoding="utf-8"))
    assert (
        config["paths"]["broker_fusion"]
        == "modules/broker_fusion/broker_fusion_publisher.py"
    )


def test_v2_writer_lock_rejects_concurrent_writer(tmp_path: Path):
    lock_path = tmp_path / "FINAL_DECISION_V2.writer.lock"

    with ArtifactWriteLock(lock_path, run_id="RUN-A"):
        assert lock_path.exists()
        with pytest.raises(RuntimeError, match="writer lock is active"):
            with ArtifactWriteLock(lock_path, run_id="RUN-B"):
                pass

    assert not lock_path.exists()


def test_run_scoped_v2_publication_has_matching_hash_and_lineage(tmp_path: Path):
    run_id = "SWING-TEST-ARTIFACT"
    run_scoped = tmp_path / "fusion" / run_id / "FINAL_DECISION_V2.csv"
    canonical = tmp_path / "input" / "FINAL_DECISION_V2.csv"
    manifest_dir = tmp_path / "manifests"
    lock_path = tmp_path / "state" / "FINAL_DECISION_V2.writer.lock"

    run_scoped.parent.mkdir(parents=True, exist_ok=True)
    run_scoped.write_bytes(b"Rank,Symbol,Run_ID\n1,BBCA,SWING-TEST-ARTIFACT\n")

    base_manifest = {
        "Run_ID": run_id,
        "technical_source_hash": "tech-hash",
        "broker_source_hash": "broker-hash",
        "broker_raw_source_hash": "raw-hash",
        "broker_date": "2026-08-13",
        "broker_matched": 40,
        "broker_expected": 40,
        "broker_coverage": 1.0,
    }

    with ArtifactWriteLock(lock_path, run_id=run_id):
        manifest = publish_run_scoped_artifact(
            run_scoped_output=run_scoped,
            canonical_output=canonical,
            base_manifest=base_manifest,
            run_id=run_id,
            manifest_dir=manifest_dir,
            lock_path=lock_path,
        )

    assert canonical.read_bytes() == run_scoped.read_bytes()
    assert manifest["publication_status"] == "PUBLISHED_ATOMIC"
    assert manifest["artifact_integrity_version"] == "V2_SERIALIZED_ATOMIC_V1"
    assert manifest["lineage_complete"] is True
    assert manifest["run_scoped_output_hash"] == file_sha256(run_scoped)
    assert manifest["canonical_output_hash"] == file_sha256(canonical)
    assert manifest["output_hash"] == file_sha256(canonical)

    canonical_manifest = json.loads(
        canonical.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    run_manifest = json.loads(
        (manifest_dir / f"BROKER_FUSION_MANIFEST_{run_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert canonical_manifest["Run_ID"] == run_id
    assert run_manifest["Run_ID"] == run_id
    assert canonical_manifest["output_hash"] == run_manifest["output_hash"]


def test_stale_v2_sidecar_write_is_blocked(tmp_path: Path):
    canonical = tmp_path / "FINAL_DECISION_V2.csv"
    canonical.write_bytes(b"Rank,Symbol\n1,BBCA\n")

    with pytest.raises(
        RuntimeError,
        match="STALE_FINAL_DECISION_V2_MANIFEST_WRITE_BLOCKED",
    ):
        write_json(
            tmp_path / "FINAL_DECISION_V2.manifest.json",
            {
                "Run_ID": "STALE-RUN",
                "output_hash": "0" * 64,
            },
        )


def test_matching_v2_sidecar_write_is_allowed(tmp_path: Path):
    canonical = tmp_path / "FINAL_DECISION_V2.csv"
    canonical.write_bytes(b"Rank,Symbol\n1,BBCA\n")
    output_hash = file_sha256(canonical)

    target = tmp_path / "FINAL_DECISION_V2.manifest.json"
    write_json(
        target,
        {
            "Run_ID": "CURRENT-RUN",
            "output_hash": output_hash,
        },
    )

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["Run_ID"] == "CURRENT-RUN"
    assert written["output_hash"] == output_hash
