from __future__ import annotations

import json

from modules.data_sources import constants as C
from modules.data_sources.health import SourceHealth, SourceHealthMonitor


def test_not_configured_when_flag_false():
    h = SourceHealth(source="ZAPI_IDX", configured=False)
    assert h.health_status == C.HEALTH_NOT_CONFIGURED


def test_not_configured_when_no_requests():
    h = SourceHealth(source="ZAPI_IDX", configured=True)
    assert h.health_status == C.HEALTH_NOT_CONFIGURED


def test_healthy_when_all_success():
    monitor = SourceHealthMonitor({"STOCKBIT": True})
    for _ in range(5):
        monitor.record_request("STOCKBIT")
        monitor.record_success("STOCKBIT", latency_ms=100.0)
    snap = monitor.snapshot()["STOCKBIT"]
    assert snap["health_status"] == C.HEALTH_HEALTHY
    assert snap["average_latency_ms"] == 100.0


def test_unavailable_when_no_success():
    monitor = SourceHealthMonitor({"STOCKBIT": True})
    for _ in range(3):
        monitor.record_request("STOCKBIT")
        monitor.record_failure("STOCKBIT", kind="timeout")
    snap = monitor.snapshot()["STOCKBIT"]
    assert snap["health_status"] == C.HEALTH_UNAVAILABLE
    assert snap["timeout_count"] == 3


def test_unavailable_when_high_failure_rate():
    monitor = SourceHealthMonitor({"STOCKBIT": True})
    for _ in range(10):
        monitor.record_request("STOCKBIT")
    for _ in range(6):
        monitor.record_failure("STOCKBIT")
    for _ in range(4):
        monitor.record_success("STOCKBIT")
    snap = monitor.snapshot()["STOCKBIT"]
    assert snap["health_status"] == C.HEALTH_UNAVAILABLE


def test_degraded_when_fallback_used():
    monitor = SourceHealthMonitor({"STOCKBIT": True})
    for _ in range(10):
        monitor.record_request("STOCKBIT")
        monitor.record_success("STOCKBIT")
    monitor.record_fallback("STOCKBIT")
    snap = monitor.snapshot()["STOCKBIT"]
    assert snap["health_status"] == C.HEALTH_DEGRADED


def test_degraded_when_validation_failures():
    monitor = SourceHealthMonitor({"STOCKBIT": True})
    for _ in range(10):
        monitor.record_request("STOCKBIT")
        monitor.record_success("STOCKBIT")
    monitor.record_failure("STOCKBIT", kind="validation")
    snap = monitor.snapshot()["STOCKBIT"]
    assert snap["validation_failure_count"] == 1
    assert snap["health_status"] == C.HEALTH_DEGRADED


def test_write_snapshot(tmp_path):
    monitor = SourceHealthMonitor({"ZAPI_IDX": False, "STOCKBIT": True})
    monitor.record_request("STOCKBIT")
    monitor.record_success("STOCKBIT", latency_ms=50.0)
    latest = monitor.write(tmp_path, run_id="20260105")
    assert latest.exists()
    data = json.loads(latest.read_text(encoding="utf-8"))
    assert "STOCKBIT" in data["sources"]
    assert data["sources"]["ZAPI_IDX"]["health_status"] == C.HEALTH_NOT_CONFIGURED
    assert (tmp_path / "SOURCE_HEALTH_20260105.json").exists()
