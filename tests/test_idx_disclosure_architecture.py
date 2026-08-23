from pathlib import Path
import json


def test_idx_disclosure_config_is_safe_by_default() -> None:
    cfg = json.loads(Path("config/idx_disclosure.json").read_text(encoding="utf-8"))
    assert cfg["enabled"] is False
    assert cfg["delivery"]["enabled"] is False
    assert cfg["delivery"]["topic_route"] == "news"
    assert cfg["delivery"]["reuse_existing_news_topic"] is True
    assert cfg["safety"]["decision_engine_write_access"] is False
    # The optional document reader is enabled in configuration, but remains
    # isolated from scoring/trading and cannot write to the decision engine.
    assert cfg["safety"]["ai_enabled"] is True
    assert cfg["ai_reader"]["enabled"] is True
    assert cfg["ai_reader"]["safety"]["decision_engine_write_access"] is False
    assert cfg["safety"]["brave_enabled"] is False


def test_idx_disclosure_runner_exists() -> None:
    assert Path("run_idx_disclosure_watcher.py").exists()
