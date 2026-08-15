from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import run_final_watchlist_playwright_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]


def _spec(period_type: str = "1D"):
    return SimpleNamespace(
        period_type=period_type,
        period_start="2026-08-14" if period_type == "1D" else "2026-08-12",
        period_end="2026-08-14",
    )


def test_entrypoint_routes_broker_period_through_playwright_bridge():
    source = (ROOT / "tools" / "run_final_watchlist_entrypoint.py").read_text(encoding="utf-8")
    normalized = source.replace("\\", "/")

    assert "tools/run_final_watchlist_playwright_bridge.py" in normalized
    assert 'str(ROOT / "tools/run_final_watchlist_broker_period.py")' not in normalized


def test_today_pulse_guard_accepts_real_current_session():
    payload = {
        "broker_period_end": "2026-08-14",
        "today_pulse_available": True,
        "today_pulse_date": "2026-08-14",
        "today_pulse_source": "STOCKBIT_1D",
    }
    assert bridge.require_today_pulse(payload) is payload


def test_today_pulse_guard_rejects_missing_pulse():
    with pytest.raises(RuntimeError, match="BROKER_TODAY_PULSE_REQUIRED:2026-08-14"):
        bridge.require_today_pulse({
            "broker_period_end": "2026-08-14",
            "today_pulse_available": False,
            "today_pulse_date": "",
            "today_pulse_source": "",
        })


def test_today_pulse_guard_rejects_aggregate_as_pulse():
    with pytest.raises(RuntimeError, match="BROKER_TODAY_PULSE_SOURCE_INVALID"):
        bridge.require_today_pulse({
            "broker_period_end": "2026-08-14",
            "today_pulse_available": True,
            "today_pulse_date": "2026-08-14",
            "today_pulse_source": "STOCKBIT_AGGREGATE_EXPORT",
        })


def test_manual_1d_timeout_is_terminalized_as_missing_pulse(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "_playwright_enabled", lambda: False)

    def timeout(*args, **kwargs):
        raise TimeoutError("manual export timeout")

    monkeypatch.setattr(bridge, "_ORIGINAL_WAIT_FOR_MATCHING_EXPORT", timeout)

    with pytest.raises(RuntimeError, match="BROKER_TODAY_PULSE_REQUIRED:2026-08-14"):
        bridge.playwright_wait_for_matching_export(
            tmp_path,
            ["BBCA"],
            0.8,
            _spec("1D"),
            timeout_seconds=1,
            poll_seconds=0.01,
        )


def test_manual_multiday_timeout_keeps_existing_timeout_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "_playwright_enabled", lambda: False)

    def timeout(*args, **kwargs):
        raise TimeoutError("manual export timeout")

    monkeypatch.setattr(bridge, "_ORIGINAL_WAIT_FOR_MATCHING_EXPORT", timeout)

    with pytest.raises(TimeoutError):
        bridge.playwright_wait_for_matching_export(
            tmp_path,
            ["BBCA"],
            0.8,
            _spec("3D"),
            timeout_seconds=1,
            poll_seconds=0.01,
        )


def test_playwright_on_bypasses_manual_wait(monkeypatch, tmp_path):
    expected = tmp_path / "BROKER_SUMMARY_COMBINED_PLAYWRIGHT_1D.csv"
    info = {"coverage": 1.0}
    calls = {"manual": 0, "auto": 0}

    monkeypatch.setattr(bridge, "_playwright_enabled", lambda: True)

    def auto(*args, **kwargs):
        calls["auto"] += 1
        return expected, info

    def manual(*args, **kwargs):
        calls["manual"] += 1
        raise AssertionError("manual waiter must not run when Playwright is ON")

    monkeypatch.setattr(bridge, "_collect_with_playwright", auto)
    monkeypatch.setattr(bridge, "_ORIGINAL_WAIT_FOR_MATCHING_EXPORT", manual)

    result = bridge.playwright_wait_for_matching_export(
        tmp_path,
        ["BBCA"],
        0.8,
        _spec("1D"),
        timeout_seconds=1,
        poll_seconds=0.01,
    )

    assert result == (expected, info)
    assert calls == {"manual": 0, "auto": 1}
