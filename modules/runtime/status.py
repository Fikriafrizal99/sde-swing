from __future__ import annotations

"""Unified status schema and writer for integrated jobs.

Commit 4 aligns this secondary RuntimeContext writer with the same terminal
invariants used by the production job-runner status path.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .context import RUNTIME_VERSION, RuntimeContext

ALLOWED_JOB_STATUSES = {
    "SUCCESS",
    "SUCCESS_WITH_WARNING",
    "PARTIAL",
    "SKIPPED",
    "FAILED",
    "NOT_CONFIGURED",
}
RUNTIME_STATUS_CONTRACT_VERSION = "SDE_RUNTIME_STATUS_V1"


def normalized_status(status: str, *, details: dict[str, Any] | None = None) -> str:
    value = str(status or "FAILED").strip().upper()
    if value in ALLOWED_JOB_STATUSES:
        return value
    if value.startswith("SKIP") or value in {
        "DUPLICATE_SUPPRESSED",
        "WAITING_DATA",
        "WAITING_DATA_TIMEOUT",
    }:
        return "SKIPPED"
    if value in {"DELIVERY_FAILED", "INVALID_DATA", "INVALID_GLOBAL_MARKET_DATA"}:
        return "FAILED"
    if value.startswith("PARTIAL") or value.endswith("WITH_WARNING"):
        return "PARTIAL"
    return "FAILED"


def _content_hash(payload: dict[str, Any]) -> str:
    import json

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def build_status_payload(
    context: RuntimeContext,
    status: str,
    current_stage: str,
    *,
    exit_code: int = 0,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details = dict(details or {})
    metadata = context.provider_metadata
    normalized = normalized_status(status, details=details)
    if normalized == "FAILED" and int(exit_code) == 0:
        exit_code = 1
        details["warnings"] = [
            *(details.get("warnings", []) or []),
            "EXIT_CODE_NORMALIZED_FROM_ZERO_FOR_FAILED_STATUS",
        ]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        "run_id": context.run_id,
        "job_name": context.job_name,
        "job_mode": context.mode,
        "status_channel": "ENGINE",
        "runtime_status_contract": RUNTIME_STATUS_CONTRACT_VERSION,
        "status": normalized,
        "engine_status": details.get("engine_status", normalized),
        "delivery_status": details.get(
            "delivery_status", details.get("telegram_status", "NOT_RUN")
        ),
        "legacy_status": str(status),
        "current_stage": current_stage,
        "trade_date": context.trade_date.isoformat(),
        "config_version": context.config_version or RUNTIME_VERSION,
        "data_status": details.get(
            "data_status", details.get("Data_Quality_Status", "NOT_AVAILABLE")
        ),
        "data_source_mode": details.get("data_source_mode")
        or metadata.get("data_source_mode")
        or "NOT_CONFIGURED",
        "primary_provider": details.get("primary_provider")
        or metadata.get("primary_provider")
        or "NOT_CONFIGURED",
        "provider_status": details.get("provider_status")
        or metadata.get("provider_status")
        or "NOT_CONFIGURED",
        "providers_attempted": details.get(
            "providers_attempted", metadata.get("providers_attempted", [])
        ),
        "fallback_used": bool(
            details.get("fallback_used", metadata.get("fallback_used", False))
        ),
        "mock_used": bool(
            details.get("mock_used", metadata.get("mock_used", False))
        ),
        "source_health": details.get(
            "source_health", metadata.get("source_health", {})
        ),
        "source_coverage_ratio": float(
            details.get(
                "source_coverage_ratio",
                metadata.get("source_coverage_ratio", 0.0),
            )
            or 0.0
        ),
        "symbols_requested": int(details.get("symbols_requested", 0) or 0),
        "symbols_loaded": int(details.get("symbols_loaded", 0) or 0),
        "symbols_valid": int(details.get("symbols_valid", 0) or 0),
        "symbols_failed": int(details.get("symbols_failed", 0) or 0),
        "symbols_skipped": int(details.get("symbols_skipped", 0) or 0),
        "snapshot_ids": details.get("snapshot_ids", {}),
        "dependency_status": details.get("dependency_status", {}),
        "warnings": list(details.get("warnings", []) or []),
        "errors": list(details.get("errors", []) or []),
        "telegram_status": details.get("telegram_status", ""),
        "created_at": now,
        "updated_at": now,
        "exit_code": int(exit_code),
        "details": details,
    }
    payload["content_hash"] = _content_hash(payload)
    return payload


class StatusWriter:
    """Writes dated and latest ENGINE status records plus an audit trail."""

    def __init__(self, context: RuntimeContext, root: Path | None = None) -> None:
        self.context = context
        self.root = root or context.paths.output("job_status")

    def write(
        self,
        status: str,
        current_stage: str,
        *,
        exit_code: int = 0,
        details: dict[str, Any] | None = None,
    ) -> Path:
        from modules.job_runner.runtime import _atomic_status_json

        payload = build_status_payload(
            self.context,
            status,
            current_stage,
            exit_code=exit_code,
            details=details,
        )
        dated = self.root / self.context.trade_date.isoformat()
        path = dated / f"{self.context.job_name}_{self.context.run_id}.json"
        _atomic_status_json(path, payload)
        _atomic_status_json(
            self.root / f"{self.context.job_name}_latest.json", payload
        )
        return path
