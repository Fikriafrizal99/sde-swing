from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from swing_utils import PACKAGE_VERSION, PIPELINE_VERSION, file_sha256

EXPECTED_STATUSES = ["BUY READY", "BUY ON TRIGGER", "WATCH", "AVOID"]
LEGACY_OVERRIDE_KEYS = {
    "override", "overrides", "runtime_override", "runtime_overrides",
    "migration", "migrations", "legacy", "legacy_config", "threshold_override",
}


class RuntimeConfigError(ValueError):
    pass


def _find_legacy_override_keys(payload: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).strip().lower()
            path = f"{prefix}.{key}" if prefix else str(key)
            if normalized in LEGACY_OVERRIDE_KEYS:
                found.append(path)
            found.extend(_find_legacy_override_keys(value, path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(_find_legacy_override_keys(value, f"{prefix}[{index}]"))
    return found


def validate_config(payload: dict[str, Any], *, strict: bool = True) -> list[str]:
    warnings: list[str] = []
    if not isinstance(payload, dict) or not payload:
        raise RuntimeConfigError("Konfigurasi pipeline kosong atau bukan objek JSON")

    package = payload.get("package")
    decision = payload.get("decision")
    candidate = payload.get("candidate")

    if not isinstance(package, dict):
        if strict:
            raise RuntimeConfigError("config.package wajib tersedia")
        warnings.append("PACKAGE_METADATA_MISSING")
    else:
        version = str(package.get("version", "")).strip()
        pipeline_version = str(package.get("pipeline_version", "")).strip()
        if version != PACKAGE_VERSION:
            raise RuntimeConfigError(
                f"CONFIG_VERSION_MISMATCH: config={version or 'EMPTY'} source={PACKAGE_VERSION}"
            )
        if pipeline_version != PIPELINE_VERSION:
            raise RuntimeConfigError(
                "PIPELINE_VERSION_MISMATCH: "
                f"config={pipeline_version or 'EMPTY'} source={PIPELINE_VERSION}"
            )

    if not isinstance(candidate, dict):
        if strict:
            raise RuntimeConfigError("config.candidate wajib tersedia")
        warnings.append("CANDIDATE_CONFIG_MISSING")
    else:
        top = int(candidate.get("top", 0) or 0)
        min_score = float(candidate.get("min_score", 0) or 0)
        if top <= 0:
            raise RuntimeConfigError("candidate.top harus lebih besar dari 0")
        if not 0 <= min_score <= 100:
            raise RuntimeConfigError("candidate.min_score harus berada pada rentang 0..100")

    if not isinstance(decision, dict):
        if strict:
            raise RuntimeConfigError("config.decision wajib tersedia")
        warnings.append("DECISION_CONFIG_MISSING")
    else:
        statuses = [str(x).strip().upper() for x in decision.get("statuses", [])]
        if statuses != EXPECTED_STATUSES:
            raise RuntimeConfigError(
                f"DECISION_STATUS_MISMATCH: expected={EXPECTED_STATUSES} actual={statuses}"
            )
        weights = decision.get("weights", {})
        if not isinstance(weights, dict) or not weights:
            raise RuntimeConfigError("decision.weights wajib tersedia")
        total = sum(float(value) for value in weights.values())
        if abs(total - 1.0) > 1e-9:
            raise RuntimeConfigError(f"DECISION_WEIGHT_SUM_INVALID: {total:.12f}")

    legacy_keys = _find_legacy_override_keys(payload)
    if legacy_keys:
        raise RuntimeConfigError(
            "UNDOCUMENTED_LEGACY_OVERRIDE_KEYS: " + ", ".join(sorted(legacy_keys))
        )
    return warnings


def load_runtime_config(path: Path, *, strict: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = path.resolve()
    if not resolved.exists() or resolved.stat().st_size == 0:
        raise RuntimeConfigError(f"Konfigurasi tidak ditemukan atau kosong: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeConfigError(f"Konfigurasi JSON tidak valid: {resolved}: {exc}") from exc

    warnings = validate_config(payload, strict=strict)
    package = payload.get("package", {}) if isinstance(payload, dict) else {}
    provenance = {
        "config_source": str(resolved),
        "config_hash": file_sha256(resolved),
        "config_version": str(package.get("version", "")),
        "pipeline_version": str(package.get("pipeline_version", "")),
        "loaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "validation_status": "VALID" if not warnings else "VALID_WITH_WARNINGS",
        "validation_warnings": warnings,
        "legacy_overrides_detected": [],
        "override_mode": "NONE",
    }
    return payload, provenance


def write_runtime_config_audit(path: Path, provenance: dict[str, Any], payload: dict[str, Any]) -> Path:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = payload.get("candidate", {})
    decision = payload.get("decision", {})
    exit_cfg = payload.get("exit", {})
    audit = {
        **provenance,
        "effective_values": {
            "candidate": candidate,
            "decision": decision,
            "exit": exit_cfg,
        },
    }
    target.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target
