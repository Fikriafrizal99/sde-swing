#!/usr/bin/env python3
"""Validate the audited SDE Swing quant freeze.

This guard is intentionally read-only. It detects drift from the audited
121bc58 production quant contract without changing runtime behavior.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PIPELINE_PATH = ROOT / "config" / "pipeline.json"
FREEZE_PATH = ROOT / "config" / "audit_quant_freeze.json"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _same_scalar(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=0.0, abs_tol=1e-12)
    return expected == actual


def _compare_subset(
    expected: Any,
    actual: Any,
    path: str,
    errors: list[str],
) -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            errors.append(f"{path}: expected mapping, got {type(actual).__name__}")
            return
        for key, expected_value in expected.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                errors.append(f"{child}: missing")
                continue
            _compare_subset(expected_value, actual[key], child, errors)
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            errors.append(f"{path}: expected list, got {type(actual).__name__}")
            return
        if expected != actual:
            errors.append(f"{path}: expected {expected!r}, got {actual!r}")
        return

    if not _same_scalar(expected, actual):
        errors.append(f"{path}: expected {expected!r}, got {actual!r}")


def _git_blob_sha(path: Path) -> str:
    content = path.read_bytes()
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def validate_quant_freeze(
    pipeline: Mapping[str, Any],
    freeze: Mapping[str, Any],
    *,
    code_profile: Mapping[str, Any] | None = None,
    code_setup_profiles: Mapping[str, Any] | None = None,
    check_source_blobs: bool = True,
) -> list[str]:
    errors: list[str] = []

    _compare_subset(
        freeze["pipeline_protected"],
        pipeline,
        "pipeline",
        errors,
    )

    decision = pipeline.get("decision", {})
    weights = decision.get("weights", {})
    weight_total = sum(float(value) for value in weights.values())
    if not math.isclose(weight_total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        errors.append(f"pipeline.decision.weights: total must be 1.0, got {weight_total!r}")

    if decision.get("production_profile") != "MODERATE_BASELINE":
        errors.append(
            "pipeline.decision.production_profile: must remain 'MODERATE_BASELINE'"
        )

    calibration = decision.get("calibration", {})
    if calibration.get("auto_entry_enabled") is not False:
        errors.append(
            "pipeline.decision.calibration.auto_entry_enabled: must remain false"
        )

    if code_profile is None or code_setup_profiles is None:
        from modules.decision_engine.moderate_profiles import (
            DEFAULT_MODERATE_PROFILES,
            DEFAULT_SETUP_PROFILES,
        )

        code_profile = DEFAULT_MODERATE_PROFILES["MODERATE_BASELINE"]
        code_setup_profiles = DEFAULT_SETUP_PROFILES

    _compare_subset(
        freeze["code_defaults"]["moderate_profile"],
        code_profile,
        "code.MODERATE_BASELINE",
        errors,
    )
    _compare_subset(
        freeze["code_defaults"]["setup_profiles"],
        code_setup_profiles,
        "code.DEFAULT_SETUP_PROFILES",
        errors,
    )

    if check_source_blobs:
        for relative_path, expected_sha in freeze.get("protected_source_blobs", {}).items():
            source_path = ROOT / relative_path
            if not source_path.exists():
                errors.append(f"source.{relative_path}: missing")
                continue
            actual_sha = _git_blob_sha(source_path)
            if actual_sha != expected_sha:
                errors.append(
                    f"source.{relative_path}: expected git blob {expected_sha}, got {actual_sha}"
                )

    return errors


def main() -> int:
    pipeline = _load_json(PIPELINE_PATH)
    freeze = _load_json(FREEZE_PATH)
    errors = validate_quant_freeze(pipeline, freeze)

    if errors:
        print("QUANT FREEZE INVALID")
        for error in errors:
            print(f"- {error}")
        return 1

    baseline = freeze["baseline"]["commit"]
    print(f"QUANT FREEZE VALID — audited baseline {baseline}")
    print("Production profile: MODERATE_BASELINE")
    print("Auto-entry: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
