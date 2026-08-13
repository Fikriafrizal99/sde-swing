#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.analytics.lifecycle_contract import LIFECYCLE_CONTRACT_VERSION
from modules.data_sources import constants as C
from modules.data_sources.canonical import DailyBar
from modules.data_sources.conflict_resolver import ConflictResolver
from modules.data_sources.legacy_daily_bar_adapter import CANONICAL_DAILY_HISTORY_CONTRACT
from modules.runtime.status import RUNTIME_STATUS_CONTRACT_VERSION

AUDITED_BASELINE = "121bc58b0f6a62dc3a844ee59575fe48ce86cc7d"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"RELEASE GATE FAILED: {message}")


def load_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def validate_quant() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/ci_validate_quant_freeze.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    require(result.returncode == 0, result.stdout + result.stderr)
    require(AUDITED_BASELINE in result.stdout, "quant validator did not prove audited baseline")


def validate_config() -> None:
    cfg = load_json("config/pipeline.json")
    decision = cfg.get("decision", {})
    calibration = decision.get("calibration", {})
    paths = cfg.get("paths", {})
    freshness = cfg.get("data_freshness", {})
    require(decision.get("production_profile") == "MODERATE_BASELINE", "production profile drift")
    require(calibration.get("auto_entry_enabled") is False, "auto-entry must remain false")
    require(calibration.get("mode") == "SHADOW_ONLY", "calibration must remain SHADOW_ONLY")
    require(paths.get("technical_feature_engine") == "modules/technical_feature_engine/post_market_validated_runner.py", "technical engine bypasses canonical wrapper")
    require(paths.get("broker_fusion") == "modules/broker_fusion/broker_fusion_publisher.py", "V2 publisher bypassed")
    require(freshness.get("daily_candle_policy") == "LAST_CLOSED_CANDLE", "closed-candle policy drift")
    require(freshness.get("allow_partial_daily_candle") is False, "partial daily candle enabled")


def validate_contract_versions() -> None:
    require(LIFECYCLE_CONTRACT_VERSION == "SDE_SWING_LIFECYCLE_V1", "lifecycle contract drift")
    require(RUNTIME_STATUS_CONTRACT_VERSION == "SDE_RUNTIME_STATUS_V1", "runtime status contract drift")
    require(CANONICAL_DAILY_HISTORY_CONTRACT == "SDE_CANONICAL_DAILY_HISTORY_V1", "canonical data contract drift")


def validate_date_conflict_fail_closed() -> None:
    common = dict(
        symbol="BBCA",
        event_timestamp="2026-08-12T16:15:00+07:00",
        received_at="2026-08-12T16:16:00+07:00",
        open=8000.0,
        high=8100.0,
        low=7950.0,
        close=8050.0,
        volume=1_000_000.0,
    )
    first = DailyBar(market_date="2026-08-12", source="PRIMARY", **common)
    second = DailyBar(market_date="2026-08-11", source="FALLBACK", **common)
    result = ConflictResolver(source_priorities={"PRIMARY": 1, "FALLBACK": 2}).resolve([first, second])
    require(result.record is None, "market-date conflict selected a winner")
    require(result.fail_closed is True, "market-date conflict did not fail closed")
    require(result.conflict_status == C.CONFLICT_FAIL_CLOSED, "market-date conflict status not fail-closed")
    require(first.quality_status == C.QUALITY_REJECTED and second.quality_status == C.QUALITY_REJECTED, "conflicting candidates not rejected")


def validate_sqlite() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        value = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    require(str(value).lower() == "ok", "SQLite integrity_check failed")


def validate_structure() -> None:
    required = [
        "docs/SDE_AUDIT_BASELINE.md",
        "docs/SDE_STABILIZATION_AUDIT_TRACEABILITY.md",
        "docs/SDE_STABILIZATION_DEFERRED_FINDINGS.md",
        "modules/broker_fusion/broker_fusion_publisher.py",
        "modules/technical_feature_engine/post_market_validated_runner.py",
        "modules/data_sources/legacy_daily_bar_adapter.py",
    ]
    for rel in required:
        require((ROOT / rel).exists(), f"required release artifact missing: {rel}")

    wrapper = (ROOT / "modules/technical_feature_engine/post_market_validated_runner.py").read_text(encoding="utf-8")
    require("DataSourceManager" in wrapper, "production technical wrapper does not construct DataSourceManager")
    require("Engine_Input_Is_Raw_Provider_Directory" in wrapper, "canonical lineage assertion missing")

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    require('"audit/**"' in workflow, "audit branch not covered by CI")
    require("ci_validate_quant_freeze.py" in workflow, "quant freeze absent from CI")


if __name__ == "__main__":
    validate_quant()
    validate_config()
    validate_contract_versions()
    validate_date_conflict_fail_closed()
    validate_sqlite()
    validate_structure()
    print(f"STABILIZATION RELEASE GATE VALID — audited baseline {AUDITED_BASELINE}")
    print("Contracts: quant + lifecycle + runtime status + canonical data")
    print("Auto-entry: false | profile: MODERATE_BASELINE | calibration: SHADOW_ONLY")
