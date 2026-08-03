from __future__ import annotations

"""Integrated job dependency graph and execution facade."""

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


def validate_dependency_status(
    context: RuntimeContext,
    job_name: str,
    statuses: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate required predecessor status, date and config version."""
    required = JOB_DEPENDENCIES.get(job_name, ())
    statuses = statuses or {}
    result: dict[str, Any] = {"required": list(required), "valid": True, "dependencies": {}}
    for dependency in required:
        payload = statuses.get(dependency)
        if payload is None:
            result["dependencies"][dependency] = {"status": "MISSING"}
            result["valid"] = False
            continue
        status = str(payload.get("status", payload.get("status_v1_7", ""))).upper()
        dep_date = str(payload.get("trade_date", ""))
        dep_config = str(payload.get("config_version", ""))
        fresh = status in {"SUCCESS", "SUCCESS_WITH_WARNING", "PARTIAL"}
        date_ok = dep_date == context.trade_date.isoformat()
        config_ok = not dep_config or dep_config == context.config_version
        result["dependencies"][dependency] = {
            "status": status,
            "trade_date": dep_date,
            "config_version": dep_config,
            "fresh": fresh,
            "date_match": date_ok,
            "config_match": config_ok,
        }
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

