from __future__ import annotations

"""Source health monitor.

Accumulates per-source telemetry and derives a health status.  Persisted to
``data/output/source_health/`` so operators can see which sources are healthy,
degraded, unavailable, or not configured.  This module never influences trading
decisions; it is observability only.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from swing_utils import write_json
from modules.data_sources import constants as C
from modules.data_sources.canonical import now_wib

DEFAULT_HEALTH_DIR = Path("data/output/source_health")


@dataclass
class SourceHealth:
    source: str
    configured: bool = True
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    rate_limit_count: int = 0
    validation_failure_count: int = 0
    fallback_count: int = 0
    total_latency_ms: float = 0.0
    last_success_at: str | None = None
    last_failure_at: str | None = None

    @property
    def average_latency_ms(self) -> float:
        return round(self.total_latency_ms / self.success_count, 2) if self.success_count else 0.0

    @property
    def health_status(self) -> str:
        if not self.configured:
            return C.HEALTH_NOT_CONFIGURED
        if self.request_count == 0:
            return C.HEALTH_NOT_CONFIGURED
        if self.success_count == 0:
            return C.HEALTH_UNAVAILABLE
        failure_rate = self.failure_count / self.request_count
        if failure_rate >= 0.5:
            return C.HEALTH_UNAVAILABLE
        if failure_rate > 0.1 or self.validation_failure_count > 0 or self.fallback_count > 0:
            return C.HEALTH_DEGRADED
        return C.HEALTH_HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "configured": self.configured,
            "request_count": self.request_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "timeout_count": self.timeout_count,
            "rate_limit_count": self.rate_limit_count,
            "validation_failure_count": self.validation_failure_count,
            "fallback_count": self.fallback_count,
            "average_latency_ms": self.average_latency_ms,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "health_status": self.health_status,
        }


class SourceHealthMonitor:
    def __init__(self, sources: dict[str, bool] | None = None) -> None:
        # sources maps name -> configured flag.
        self._health: dict[str, SourceHealth] = {}
        for name, configured in (sources or {}).items():
            self._health[name] = SourceHealth(source=name, configured=configured)

    def _get(self, source: str) -> SourceHealth:
        if source not in self._health:
            self._health[source] = SourceHealth(source=source)
        return self._health[source]

    def record_request(self, source: str) -> None:
        self._get(source).request_count += 1

    def record_success(self, source: str, *, latency_ms: float = 0.0) -> None:
        h = self._get(source)
        h.success_count += 1
        h.total_latency_ms += latency_ms
        h.last_success_at = now_wib().isoformat()

    def record_failure(self, source: str, *, kind: str = "") -> None:
        h = self._get(source)
        h.failure_count += 1
        h.last_failure_at = now_wib().isoformat()
        if kind == "timeout":
            h.timeout_count += 1
        elif kind == "rate_limit":
            h.rate_limit_count += 1
        elif kind == "validation":
            h.validation_failure_count += 1

    def record_fallback(self, source: str) -> None:
        self._get(source).fallback_count += 1

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: h.to_dict() for name, h in sorted(self._health.items())}

    def write(self, output_dir: Path | str = DEFAULT_HEALTH_DIR, *, run_id: str = "") -> Path:
        out_dir = Path(output_dir)
        payload = {
            "generated_at": now_wib().isoformat(),
            "run_id": run_id,
            "sources": self.snapshot(),
        }
        latest = out_dir / "SOURCE_HEALTH_LATEST.json"
        write_json(latest, payload)
        if run_id:
            write_json(out_dir / f"SOURCE_HEALTH_{run_id}.json", payload)
        return latest