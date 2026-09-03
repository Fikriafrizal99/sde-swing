#!/usr/bin/env python3
"""Validate the dedicated PULLBACK freeze for the testing branch.

This guard is intentionally read-only. It protects the PULLBACK technical
contract and entry/risk implementation while allowing broker-window research
(such as 1D versus 3D) to continue outside the frozen core.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci_validate_quant_freeze import _git_blob_sha

PIPELINE_PATH = ROOT / "config" / "pipeline.json"
FREEZE_PATH = ROOT / "config" / "pullback_logic_freeze.json"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _same_scalar(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=0.0, abs_tol=1e-12)
    return expected == actual


def _compare_subset(expected: Any, actual: Any, path: str, errors: list[str]) -> None:
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
    if not _same_scalar(expected, actual):
        errors.append(f"{path}: expected {expected!r}, got {actual!r}")


def validate_pullback_freeze(
    pipeline: Mapping[str, Any],
    freeze: Mapping[str, Any],
    *,
    check_source_blobs: bool = True,
    source_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []

    if freeze.get("status") != "FROZEN":
        errors.append("freeze.status: must remain 'FROZEN'")

    contract = freeze.get("protected_contract", {})
    candidate = pipeline.get("candidate", {})
    setup_min_scores = candidate.get("setup_min_scores", {}) if isinstance(candidate, Mapping) else {}
    expected_candidate_min = contract.get("candidate_min_score")
    actual_candidate_min = setup_min_scores.get("PULLBACK") if isinstance(setup_min_scores, Mapping) else None
    if not _same_scalar(expected_candidate_min, actual_candidate_min):
        errors.append(
            f"pipeline.candidate.setup_min_scores.PULLBACK: expected {expected_candidate_min!r}, got {actual_candidate_min!r}"
        )

    decision = pipeline.get("decision", {})
    setup_profiles = decision.get("setup_profiles", {}) if isinstance(decision, Mapping) else {}
    actual_pullback_profile = setup_profiles.get("PULLBACK", {}) if isinstance(setup_profiles, Mapping) else {}
    _compare_subset(
        contract.get("setup_profile", {}),
        actual_pullback_profile,
        "pipeline.decision.setup_profiles.PULLBACK",
        errors,
    )

    from modules.decision_engine.moderate_profiles import DEFAULT_SETUP_PROFILES

    _compare_subset(
        contract.get("setup_profile", {}),
        DEFAULT_SETUP_PROFILES.get("PULLBACK", {}),
        "code.DEFAULT_SETUP_PROFILES.PULLBACK",
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
                actual_sha = _git_blob_sha(source_path, repository_path=relative_path)
            except (OSError, RuntimeError) as exc:
                errors.append(f"source.{relative_path}: unable to hash canonical content: {exc}")
                continue
            if actual_sha != expected_sha:
                errors.append(
                    f"source.{relative_path}: PULLBACK freeze drift; expected git blob {expected_sha}, got {actual_sha}"
                )

    return errors


def main() -> int:
    pipeline = _load_json(PIPELINE_PATH)
    freeze = _load_json(FREEZE_PATH)
    errors = validate_pullback_freeze(pipeline, freeze)

    if errors:
        print("PULLBACK FREEZE INVALID")
        for error in errors:
            print(f"- {error}")
        return 1

    evidence = freeze.get("performance_evidence", {})
    print(f"PULLBACK FREEZE VALID — {freeze.get('freeze_id')}")
    print(
        "Evidence: "
        f"closed={evidence.get('closed_trades')} | "
        f"WR={evidence.get('win_rate_pct')}% | "
        f"PF={evidence.get('profit_factor')} | "
        f"avg={evidence.get('avg_return_pct')}%"
    )
    print("Broker 1D/3D research remains outside the dedicated PULLBACK freeze.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
