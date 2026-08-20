from __future__ import annotations

from datetime import datetime
from pathlib import Path
import importlib.util
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_scheduled_runner_uses_canonical_entrypoints() -> None:
    module = _load_module("scheduled_job_test", ROOT / "tools" / "run_scheduled_job.py")

    market = module.canonical_command("market_outlook", [])
    post = module.canonical_command("post_market", [])
    final = module.canonical_command("final_watchlist", [])

    assert market[2].endswith("run_sde_job_integrated.py")
    assert post[2].endswith("run_sde_job_integrated_market_first.py")
    assert Path(final[2]).resolve() == (
        ROOT / "tools" / "run_final_watchlist_entrypoint.py"
    ).resolve()
    assert market[-2:] == ["--job", "market_outlook"]
    assert post[-2:] == ["--job", "post_market"]
    assert final[-2:] == ["--period", "1D"]


def test_retry_policy_normalizes_skip_and_retries_transient_codes() -> None:
    module = _load_module("scheduled_job_retry_test", ROOT / "tools" / "run_scheduled_job.py")
    cfg = {
        "scheduler_runtime": {
            "success_equivalent_exit_codes": [0, 10, 30],
            "retry": {
                "retryable_exit_codes": [20, 40, 50],
                "generic_failure_exit_codes": [1],
                "max_attempts": 3,
                "generic_failure_max_attempts": 2,
                "backoff_seconds": [60, 180],
            },
            "jobs": {},
        }
    }

    assert module.classify_exit(cfg, "market_outlook", 10, 1) == ("SKIPPED", False, 0)
    assert module.classify_exit(cfg, "market_outlook", 30, 1) == ("DUPLICATE", False, 0)
    assert module.classify_exit(cfg, "market_outlook", 20, 1)[1] is True
    assert module.classify_exit(cfg, "market_outlook", 40, 1)[1] is True
    assert module.classify_exit(cfg, "market_outlook", 50, 1)[1] is True
    assert module.classify_exit(cfg, "market_outlook", 1, 1)[1] is True
    assert module.classify_exit(cfg, "market_outlook", 1, 2)[1] is False
    assert module.classify_exit(
        cfg, "market_outlook", 10, 1, lock_busy=True
    ) == ("JOB_LOCKED", True, 10)


def test_lateness_is_observable_not_a_skip_gate() -> None:
    module = _load_module("scheduled_job_late_test", ROOT / "tools" / "run_scheduled_job.py")
    cfg = {"market_outlook": {"time": "07:30"}}
    now = datetime(2026, 8, 20, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    assert module.start_lateness_minutes("market_outlook", cfg, now) == 30.0


def test_scheduler_launchers_use_reliable_wrapper() -> None:
    for filename, job in (
        ("SCHEDULE_MARKET_OUTLOOK.bat", "market_outlook"),
        ("SCHEDULE_POST_MARKET.bat", "post_market"),
        ("SCHEDULE_FINAL_WATCHLIST.bat", "final_watchlist"),
    ):
        text = (ROOT / "scheduler" / filename).read_text(encoding="utf-8")
        assert "tools\\run_scheduled_job.py" in text
        assert f"--job {job}" in text
        assert "set_python_cmd.bat" in text

    idx = (ROOT / "scheduler" / "SCHEDULE_IDX_DISCLOSURE.bat").read_text(encoding="utf-8")
    assert "set_python_cmd.bat" in idx
    assert "--watch --telegram --transport playwright" in idx
    assert "pause" not in idx.lower()


def test_xml_generator_reads_config_and_adds_recovery_settings(tmp_path: Path) -> None:
    module = _load_module("scheduler_xml_test", ROOT / "generate_task_scheduler_xml.py")
    cfg = {
        "market_outlook": {"time": "07:45"},
        "post_market": {"time": "16:40"},
        "final_watchlist": {"start_time": "18:10"},
        "scheduler_runtime": {
            "jobs": {
                "market_outlook": {"execution_time_limit": "PT2H"},
                "post_market": {"execution_time_limit": "PT2H30M"},
                "final_watchlist": {"execution_time_limit": "PT3H"},
                "idx_disclosure": {"execution_time_limit": "PT0S"},
            }
        },
    }
    specs = module.task_specs(cfg)
    assert [spec.start_time for spec in specs[:3]] == ["07:45", "16:40", "18:10"]
    assert specs[3].trigger_type == "logon"

    for spec in specs:
        path = tmp_path / spec.launcher
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("@echo off\n", encoding="utf-8")

    tree = module.build_task(
        tmp_path,
        specs[0],
        restart_count=3,
        restart_interval="PT5M",
        multiple_instances_policy="IgnoreNew",
        start_when_available=True,
        wake_to_run=True,
    )
    root = tree.getroot()
    ns = {"t": module.NS}
    assert root.findtext("t:Settings/t:StartWhenAvailable", namespaces=ns) == "true"
    assert root.findtext("t:Settings/t:WakeToRun", namespaces=ns) == "true"
    assert root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
    assert root.findtext("t:Settings/t:RestartOnFailure/t:Count", namespaces=ns) == "3"
    assert root.findtext("t:Settings/t:RestartOnFailure/t:Interval", namespaces=ns) == "PT5M"
