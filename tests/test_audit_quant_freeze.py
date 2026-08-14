from __future__ import annotations

import copy
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "tools" / "ci_validate_quant_freeze.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("ci_validate_quant_freeze", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audited_quant_freeze_matches_current_contract():
    validator = _load_validator()
    pipeline = validator._load_json(validator.PIPELINE_PATH)
    freeze = validator._load_json(validator.FREEZE_PATH)

    errors = validator.validate_quant_freeze(pipeline, freeze)

    assert errors == []


def test_audited_quant_freeze_rejects_weight_drift():
    validator = _load_validator()
    pipeline = validator._load_json(validator.PIPELINE_PATH)
    freeze = validator._load_json(validator.FREEZE_PATH)
    tampered = copy.deepcopy(pipeline)
    tampered["decision"]["weights"]["technical_quality"] = 0.43

    errors = validator.validate_quant_freeze(tampered, freeze)

    assert any("technical_quality" in error for error in errors)
    assert any("total must be 1.0" in error for error in errors)


def test_release_version_metadata_does_not_weaken_quant_contract():
    validator = _load_validator()
    pipeline = validator._load_json(validator.PIPELINE_PATH)
    freeze = validator._load_json(validator.FREEZE_PATH)
    release_only_change = copy.deepcopy(pipeline)
    release_only_change["config_version"] = "9.9.9-release-metadata-test"

    errors = validator.validate_quant_freeze(
        release_only_change,
        freeze,
        check_source_blobs=False,
    )

    assert errors == []


def test_source_blob_hash_is_identical_for_lf_and_crlf(tmp_path: Path):
    validator = _load_validator()
    freeze = validator._load_json(validator.FREEZE_PATH)
    relative_path = "modules/decision_engine/moderate_profiles.py"
    expected = freeze["protected_source_blobs"][relative_path]
    assert expected == "9058c55b635ea8e49f8b5b074312371a321c04b9"

    canonical_lf = (ROOT / relative_path).read_bytes().replace(b"\r\n", b"\n")
    lf_path = tmp_path / "protected_lf.py"
    crlf_path = tmp_path / "protected_crlf.py"
    lf_path.write_bytes(canonical_lf)
    crlf_path.write_bytes(canonical_lf.replace(b"\n", b"\r\n"))

    assert validator._git_blob_sha(lf_path, repository_path=relative_path) == expected
    assert validator._git_blob_sha(crlf_path, repository_path=relative_path) == expected


def test_quant_freeze_rejects_actual_protected_source_mutation(tmp_path: Path):
    validator = _load_validator()
    pipeline = validator._load_json(validator.PIPELINE_PATH)
    freeze = validator._load_json(validator.FREEZE_PATH)
    source_root = tmp_path / "protected-tree"
    protected = freeze["protected_source_blobs"]

    for relative_path in protected:
        source = ROOT / relative_path
        target = source_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))

    mutated_relative = "modules/decision_engine/moderate_profiles.py"
    mutated = source_root / mutated_relative
    mutated.write_bytes(mutated.read_bytes() + b"\n# deliberate protected quant mutation\n")

    errors = validator.validate_quant_freeze(
        pipeline,
        freeze,
        source_root=source_root,
    )

    assert any(f"source.{mutated_relative}" in error for error in errors)
    assert any("expected git blob" in error for error in errors)
