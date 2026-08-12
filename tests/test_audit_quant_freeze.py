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
