#!/usr/bin/env python3
"""Validate the audited SDE Swing quant freeze.

This guard is intentionally read-only. It detects drift from the audited
121bc58 production quant contract without changing runtime behavior.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PIPELINE_PATH = ROOT / "config" / "pipeline.json"
FREEZE_PATH = ROOT / "config" / "audit_quant_freeze.json"
NON_QUANT_PIPELINE_METADATA_KEYS = frozenset({"config_version"})


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


def _git_blob_sha(
    path: Path,
    *,
    repository_path: str | Path | None = None,
) -> str:
    """Hash working-tree content after canonical Git clean normalization.

    ``core.autocrlf=input`` makes text normalization deterministic on Windows
    and Linux. ``--path`` still applies repository attributes/clean filters,
    while omitting ``-w`` keeps this validation read-only.
    """

    source = Path(path).resolve()
    if repository_path is None:
        try:
            relative = source.relative_to(ROOT.resolve()).as_posix()
        except ValueError as exc:
            raise RuntimeError(
                f"repository_path is required for a source outside {ROOT}"
            ) from exc
    else:
        relative = Path(repository_path).as_posix()
        while relative.startswith("./"):
            relative = relative[2:]
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise RuntimeError(f"invalid canonical repository path: {relative!r}")

    result = subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=input",
            "hash-object",
            f"--path={relative}",
            "--",
            str(source),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    digest = result.stdout.strip().lower()
    if result.returncode != 0 or len(digest) != 40 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        detail = (result.stderr or result.stdout).strip() or "unknown git hash-object error"
        raise RuntimeError(detail)
    return digest


def validate_quant_freeze(
    pipeline: Mapping[str, Any],
    freeze: Mapping[str, Any],
    *,
    code_profile: Mapping[str, Any] | None = None,
    code_setup_profiles: Mapping[str, Any] | None = None,
    check_source_blobs: bool = True,
    source_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []

    # The immutable audit file records the release config_version that existed
    # at audit time. Release identity is not a quant parameter, so exclude only
    # that named metadata key while preserving every protected trading field.
    protected_pipeline = dict(freeze["pipeline_protected"])
    for metadata_key in NON_QUANT_PIPELINE_METADATA_KEYS:
        protected_pipeline.pop(metadata_key, None)

    _compare_subset(
        protected_pipeline,
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
        protected_root = Path(source_root).resolve() if source_root else ROOT
        for relative_path, expected_sha in freeze.get("protected_source_blobs", {}).items():
            source_path = protected_root / relative_path
            if not source_path.exists():
                errors.append(f"source.{relative_path}: missing")
                continue
            try:
                actual_sha = _git_blob_sha(
                    source_path,
                    repository_path=relative_path,
                )
            except (OSError, RuntimeError) as exc:
                errors.append(f"source.{relative_path}: unable to hash canonical content: {exc}")
                continue
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
