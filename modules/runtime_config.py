from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from swing_utils import PACKAGE_VERSION, PIPELINE_VERSION, file_sha256, write_json
from modules.market_calendar.idx_calendar import normalized_holidays

EXPECTED_STATUSES = ["BUY READY", "BUY ON TRIGGER", "WATCH", "AVOID"]
LEGACY_OVERRIDE_KEYS = {
    "override", "overrides", "runtime_override", "runtime_overrides",
    "migration", "migrations", "legacy", "legacy_config", "threshold_override",
}

OPERATIONAL_BROKER_MIN_COVERAGE = 0.80


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


def _apply_operational_broker_policy(payload: dict[str, Any], warnings: list[str]) -> None:
    """Turn the old 100% shadow gate into an operational minimum.

    Full coverage remains the ideal quality target, but it is no longer a hard
    runtime requirement. Missing broker symbols continue through fusion as
    explicit NO DATA rows and cannot receive an accumulation conclusion.
    """
    broker = payload.get("broker")
    candidate = payload.get("candidate")
    if not isinstance(broker, dict) or not isinstance(candidate, dict):
        return

    top = max(int(candidate.get("top", 0) or 0), 1)
    configured = float(broker.get("min_coverage", OPERATIONAL_BROKER_MIN_COVERAGE) or 0.0)
    effective = min(max(configured, OPERATIONAL_BROKER_MIN_COVERAGE), 1.0)

    # The Stage-2 shadow release gate used 1.0/40-of-40. Production execution
    # now uses 80% as its minimum while preserving 100% as the ideal target.
    if effective >= 1.0:
        effective = OPERATIONAL_BROKER_MIN_COVERAGE
        warnings.append("BROKER_100_PERCENT_SHADOW_GATE_REPLACED_BY_OPERATIONAL_80_PERCENT")

    broker["min_coverage"] = effective
    broker["allow_partial_broker"] = True
    broker["required_matched_count"] = max(1, math.ceil(top * effective))
    broker["ideal_coverage"] = 1.0
    broker["coverage_policy"] = "OPERATIONAL_PARTIAL_ALLOWED"


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

    freshness = payload.get("data_freshness", {})
    holidays = freshness.get("market_holidays", []) if isinstance(freshness, dict) else []
    if strict and not holidays:
        raise RuntimeConfigError("data_freshness.market_holidays wajib tersedia")
    try:
        normalized_holidays(holidays)
    except Exception as exc:
        raise RuntimeConfigError(f"MARKET_HOLIDAY_INVALID: {exc}") from exc

    _apply_operational_broker_policy(payload, warnings)
    broker = payload.get("broker", {})
    if isinstance(broker, dict) and isinstance(candidate, dict):
        coverage = float(broker.get("min_coverage", 0.0) or 0.0)
        required = int(broker.get("required_matched_count", 0) or 0)
        top = int(candidate.get("top", 0) or 0)
        minimum_required = max(1, math.ceil(top * coverage))
        if not OPERATIONAL_BROKER_MIN_COVERAGE <= coverage <= 1.0:
            raise RuntimeConfigError(
                f"BROKER_COVERAGE_OUTSIDE_OPERATIONAL_RANGE: {coverage:.4f}"
            )
        if required < minimum_required:
            raise RuntimeConfigError(
                f"BROKER_REQUIRED_MATCHED_COUNT_TOO_LOW: required={required} minimum={minimum_required}"
            )
        if coverage < 1.0:
            warnings.append(
                f"BROKER_PARTIAL_COVERAGE_ALLOWED:{required}/{top}:{coverage:.0%}"
            )

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
        profiles = decision.get("profiles", {})
        production_profile = str(decision.get("production_profile", "")).strip().upper()
        shadow_profiles = [str(value).strip().upper() for value in decision.get("shadow_profiles", [])]
        if production_profile and production_profile not in profiles:
            raise RuntimeConfigError(f"PRODUCTION_PROFILE_NOT_CONFIGURED: {production_profile}")
        for profile_name in shadow_profiles:
            if profile_name not in profiles:
                raise RuntimeConfigError(f"SHADOW_PROFILE_NOT_CONFIGURED: {profile_name}")
        for profile_name, profile_payload in profiles.items():
            if not isinstance(profile_payload, dict):
                raise RuntimeConfigError(f"PROFILE_CONFIG_INVALID: {profile_name}")
            profile_weights = profile_payload.get("weights", {})
            if not isinstance(profile_weights, dict) or not profile_weights:
                raise RuntimeConfigError(f"PROFILE_WEIGHTS_MISSING: {profile_name}")
            profile_total = sum(float(value) for value in profile_weights.values())
            if abs(profile_total - 1.0) > 1e-9:
                raise RuntimeConfigError(f"PROFILE_WEIGHT_SUM_INVALID[{profile_name}]: {profile_total:.12f}")
        calibration = decision.get("calibration", {})
        if calibration and bool(calibration.get("auto_entry_enabled", False)):
            raise RuntimeConfigError("AUTO_ENTRY_MUST_REMAIN_DISABLED_DURING_CALIBRATION")
        portfolio = decision.get("portfolio", {})
        if not isinstance(portfolio, dict) or float(portfolio.get("reference_capital", 0) or 0) <= 0:
            raise RuntimeConfigError("DECISION_PORTFOLIO_REFERENCE_CAPITAL_INVALID")
        if not 0 < float(portfolio.get("max_position_pct", 0) or 0) <= 1:
            raise RuntimeConfigError("DECISION_MAX_POSITION_PCT_INVALID")
        if not 0 < float(portfolio.get("max_market_participation_pct", 0) or 0) <= 1:
            raise RuntimeConfigError("DECISION_MARKET_PARTICIPATION_INVALID")
        micro = decision.get("microstructure", {})
        if int(micro.get("minimum_required_metrics", 0) or 0) < 1:
            raise RuntimeConfigError("MICROSTRUCTURE_MINIMUM_REQUIRED_METRICS_INVALID")

    legacy_keys = _find_legacy_override_keys(payload)
    if legacy_keys:
        raise RuntimeConfigError(
            "UNDOCUMENTED_LEGACY_OVERRIDE_KEYS: " + ", ".join(sorted(legacy_keys))
        )
    return list(dict.fromkeys(warnings))


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
    broker = payload.get("broker", {})
    decision = payload.get("decision", {})
    exit_cfg = payload.get("exit", {})
    audit = {
        **provenance,
        "effective_values": {
            "candidate": candidate,
            "broker": broker,
            "decision": decision,
            "exit": exit_cfg,
        },
    }
    write_json(target, audit)
    return target
