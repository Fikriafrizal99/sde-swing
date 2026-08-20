from pathlib import Path
import json


def test_idx_disclosure_config_is_safe_by_default() -> None:
    cfg = json.loads(Path("config/idx_disclosure.json").read_text(encoding="utf-8"))
    assert cfg["enabled"] is False
    assert cfg["delivery"]["enabled"] is False
    assert cfg["safety"]["decision_engine_write_access"] is False
    assert cfg["safety"]["ai_enabled"] is False
    assert cfg["safety"]["brave_enabled"] is False


def test_idx_disclosure_runner_exists() -> None:
    assert Path("run_idx_disclosure_watcher.py").exists()
