from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from modules.data_sources.config import DataSourceConfig
from modules.job_runner.runtime_baseline import RUNTIME_CONFIG_VERSION
from modules.runtime.context import RUNTIME_VERSION
from run_sde_job import _official_runtime
from swing_utils import DISPLAY_VERSION, PACKAGE_VERSION, PIPELINE_VERSION


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "1.7.1"


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_primary_active_version_authorities_agree():
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == EXPECTED_VERSION
    assert PACKAGE_VERSION == EXPECTED_VERSION
    assert PIPELINE_VERSION == EXPECTED_VERSION
    assert DISPLAY_VERSION == f"SDE Swing V{EXPECTED_VERSION}"
    assert RUNTIME_CONFIG_VERSION == EXPECTED_VERSION
    assert RUNTIME_VERSION == EXPECTED_VERSION
    assert DataSourceConfig().config_version == EXPECTED_VERSION

    pipeline = _json("config/pipeline.json")
    assert pipeline["config_version"] == EXPECTED_VERSION
    assert pipeline["package"]["version"] == EXPECTED_VERSION
    assert pipeline["package"]["pipeline_version"] == EXPECTED_VERSION

    for config_path in (
        "config/data_sources.json",
        "config/global_market.json",
        "config/scheduler.json",
    ):
        assert _json(config_path)["config_version"] == EXPECTED_VERSION


def test_v171_runtime_uses_official_path_and_legacy_context_does_not():
    current = SimpleNamespace(config_provenance={"config_version": EXPECTED_VERSION})
    prior = SimpleNamespace(config_provenance={"config_version": "1.7.0-multisource"})
    unversioned = SimpleNamespace(config_provenance={})

    assert _official_runtime(current) is True
    assert _official_runtime(prior) is False
    assert _official_runtime(unversioned) is False
