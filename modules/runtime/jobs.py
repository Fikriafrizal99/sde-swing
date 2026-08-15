from __future__ import annotations

"""Integrated job dependency graph and execution facade."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from .context import RuntimeContext
from .status import StatusWriter

INTEGRATED_JOB_NAMES = (
    "pre_market",
    "market_outlook",
    "post_market",
    "technical_snapshot",
    "broker_summary",
    "broker_multi_day",
    "universe_selection",
    "candidate_selection",
    "final_watchlist",
    "final_decision",
    "telegram_delivery",
    "job_status",
)

JOB_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "pre_market": (),
    "market_outlook": ("pre_market",),
    "post_market": ("market_outlook",),
    "technical_snapshot": ("post_market",),
    "broker_summary": ("technical_snapshot",),
    "broker_multi_day": ("broker_summary",),
    "universe_selection": ("technical_snapshot",),
    "candidate_selection": ("universe_selection",),
    "final_watchlist": ("market_outlook", "post_market", "broker_summary", "broker_multi_day"),
    "final_decision": ("final_watchlist",),
    "telegram_delivery": ("final_watchlist", "final_decision"),
    "job_status": (),
}

DEPENDENCY_READY_STATUSES = {"SUCCESS", "SUCCESS_WITH_WARNING", "PARTIAL"}
FINAL_WATCHLIST_ENGINE_COMPLETE_STAGES = {
    "market_outlook": "MARKET_OUTLOOK",
    "post_market": "POST_MARKET",
}


@dataclass(frozen=True)
class JobDefinition:
    name: str
    dependencies: tuple[str, ...] = ()
    data_record_types: tuple[str, ...] = ()
    topic: str = "SYSTEM"


DEFAULT_JOB_DEFINITIONS: dict[str, JobDefinition] = {
    name: JobDefinition(
        name=name,
        dependencies=JOB_DEPENDENCIES.get(name, ()),
        data_record_types=("MarketIndex",) if name == "market_outlook" else (("DailyBar",) if name in {"post_market", "technical_snapshot"} else (("BrokerFlow", "ForeignFlow") if name in {"broker_summary", "broker_multi_day"} else ())),
        topic="SIGNAL" if name in {"final_watchlist", "final_decision"} else ("REPORT" if name not in {"pre_market", "telegram_delivery", "job_status"} else "SYSTEM"),
    )
    for name in INTEGRATED_JOB_NAMES
}


def _payload_status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status", payload.get("status_v1_7", ""))).upper()


def _payload_stage(payload: Mapping[str, Any]) -> str:
    return str(payload.get("stage", payload.get("stage_v1_7", ""))).upper()


def _dated_dependency_status(
    context: RuntimeContext,
    dependency: str,
    *,
    ready_only: bool = False,
) -> Mapping[str, Any] | None:
    """Return the newest persisted status for the effective trade date.

    Final Watchlist can run on the latest completed trading session while the
    calendar date is already a weekend/holiday. In that case ``*_latest.json``
    may legitimately point at a later skipped attempt. The runtime already
    keeps immutable per-trade-date status files, so use that history instead of
    weakening the dependency date/status/config contract.

    ``ready_only`` is used only for harmless same-date reruns that were skipped
    before executing the engine. It searches backward for the newest completed
    predecessor from the same effective trading date; genuine engine failures
    are never replaced by an older success.
    """
    configured_root = str(
        (context.scheduler_config.get("paths", {}) or {}).get(
            "job_status_root", "data/output/job_status"
        )
    ).strip() or "data/output/job_status"
    dated_root = context.paths.resolve(configured_root) / context.trade_date.isoformat()
    if not dated_root.exists():
        return None

    try:
        candidates = sorted(
            dated_root.glob(f"{dependency}_*.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return None

    expected_date = context.trade_date.isoformat()
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload_job = str(payload.get("job") or payload.get("job_name") or "").strip()
        if payload_job and payload_job != dependency:
            continue
        if str(payload.get("trade_date", "")) != expected_date:
            continue
        if ready_only and _payload_status(payload) not in DEPENDENCY_READY_STATUSES:
            continue
        return payload
    return None


def _final_watchlist_dependency_payload(
    context: RuntimeContext,
    dependency: str,
    payload: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any] | None, str]:
    """Reconcile non-engine terminal outcomes for Final Watchlist only.

    Market Outlook/Post Market write their canonical engine artifacts before
    Telegram delivery. A DELIVERY_FAILED terminal status at the engine's final
    stage therefore means the predecessor computation completed; delivery is
    not an engine dependency of Final Watchlist. Likewise, a later same-date
    run that was SKIPPED at DEPENDENCY_VALIDATION did not execute or invalidate
    the previously completed same-date engine output, so the newest ready
    historical status may be used.

    FAILED/WAITING/guardrail/exception outcomes remain authoritative.
    """
    if payload is None:
        return payload, ""

    status = _payload_status(payload)
    stage = _payload_stage(payload)
    expected_stage = FINAL_WATCHLIST_ENGINE_COMPLETE_STAGES.get(dependency, "")

    if status == "DELIVERY_FAILED" and expected_stage and stage == expected_stage:
        normalized = dict(payload)
        normalized["status"] = "SUCCESS_WITH_WARNING"
        normalized["status_v1_7"] = "SUCCESS_WITH_WARNING"
        normalized["dependency_source_status"] = status
        normalized["dependency_status_override"] = "ENGINE_COMPLETE_DELIVERY_FAILED"
        return normalized, "ENGINE_COMPLETE_DELIVERY_FAILED"

    if status == "SKIPPED" and stage == "DEPENDENCY_VALIDATION":
        previous = _dated_dependency_status(context, dependency, ready_only=True)
        if previous is not None:
            normalized = dict(previous)
            normalized["dependency_source_status"] = status
            normalized["dependency_status_override"] = "SAME_DATE_SKIPPED_REUSE_COMPLETED_STATUS"
            return normalized, "SAME_DATE_SKIPPED_REUSE_COMPLETED_STATUS"

    return payload, ""


def validate_dependency_status(
    context: RuntimeContext,
    job_name: str,
    statuses: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate required predecessor status, date and config version."""
    required = JOB_DEPENDENCIES.get(job_name, ())
    statuses = statuses or {}
    result: dict[str, Any] = {"required": list(required), "valid": True, "dependencies": {}}
    expected_date = context.trade_date.isoformat()
    for dependency in required:
        payload: Mapping[str, Any] | None = statuses.get(dependency)
        override = ""

        # Scope this reconciliation to Final Watchlist only. A later
        # weekend/holiday attempt may overwrite ``*_latest.json`` even though
        # the required completed-session status remains durably stored under
        # ``job_status/<trade_date>/``.
        if job_name == "final_watchlist" and (
            payload is None or str(payload.get("trade_date", "")) != expected_date
        ):
            dated_payload = _dated_dependency_status(context, dependency)
            if dated_payload is not None:
                payload = dated_payload
                override = "EFFECTIVE_TRADE_DATE_STATUS"

        if job_name == "final_watchlist" and dependency in FINAL_WATCHLIST_ENGINE_COMPLETE_STAGES:
            payload, final_override = _final_watchlist_dependency_payload(
                context,
                dependency,
                payload,
            )
            if final_override:
                override = final_override

        if payload is None:
            result["dependencies"][dependency] = {"status": "MISSING"}
            result["valid"] = False
            continue
        status = _payload_status(payload)
        dep_date = str(payload.get("trade_date", ""))
        dep_config = str(payload.get("config_version", ""))
        fresh = status in DEPENDENCY_READY_STATUSES
        date_ok = dep_date == expected_date
        config_ok = not dep_config or dep_config == context.config_version
        dependency_result = {
            "status": status,
            "trade_date": dep_date,
            "config_version": dep_config,
            "fresh": fresh,
            "date_match": date_ok,
            "config_match": config_ok,
        }
        source_status = str(payload.get("dependency_source_status", "")).upper()
        payload_override = str(payload.get("dependency_status_override", ""))
        if source_status:
            dependency_result["source_status"] = source_status
        if override or payload_override:
            dependency_result["dependency_status_override"] = override or payload_override
        result["dependencies"][dependency] = dependency_result
        if not (fresh and date_ok and config_ok):
            result["valid"] = False
    return result


class IntegratedJobRunner:
    """Execute a registered callable under one context and status writer."""

    def __init__(self, context: RuntimeContext, handlers: Mapping[str, Callable[[RuntimeContext], Any]] | None = None) -> None:
        self.context = context
        self.handlers = dict(handlers or {})
        self.status_writer = StatusWriter(context)

    def run(self, job_name: str) -> tuple[int, dict[str, Any]]:
        if job_name not in INTEGRATED_JOB_NAMES:
            raise ValueError(f"UNKNOWN_JOB: {job_name}")
        handler = self.handlers.get(job_name)
        if handler is None:
            payload = self.status_writer.write(
                "NOT_CONFIGURED",
                "JOB_HANDLER",
                exit_code=10,
                details={"errors": [f"JOB_HANDLER_NOT_REGISTERED:{job_name}"]},
            )
            return 10, {"status": "NOT_CONFIGURED", "path": str(payload)}
        try:
            result = handler(self.context)
            details = result if isinstance(result, dict) else {"result": result}
            status = str(details.pop("status", "SUCCESS"))
            code = int(details.pop("exit_code", 0) or 0)
        except Exception as exc:
            status, code, details = "FAILED", 1, {"errors": [str(exc)]}
        path = self.status_writer.write(status, job_name.upper(), exit_code=code, details=details)
        return code, {"status": status, "path": str(path), **details}


def new_context(job_name: str, trade_date: date, *, root: Path | None = None, mode: str = "MOCK") -> RuntimeContext:
    return RuntimeContext.create(job_name, trade_date, mode=mode, root=root)
