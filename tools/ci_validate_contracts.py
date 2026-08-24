#!/usr/bin/env python3
"""Validate canonical schema, runtime ownership, and broker-period boundaries.

The Final Watchlist contract has one exact PRIMARY Broker Fusion source and an
optional exact 1D TODAY presentation pulse.  This guard prevents retired
database-derived broker-multiday artifacts from returning to that production
path while leaving archive and portfolio modules independent.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.data_sources.canonical import RECORD_TYPES  # noqa: E402
from modules.data_sources.config import load_data_source_config  # noqa: E402
from modules.decision.candidate import FINAL_ACTIONS, CanonicalCandidate, validate_candidate  # noqa: E402
from modules.runtime.data_source_manager import DataSourceManager  # noqa: E402
from modules.runtime.jobs import INTEGRATED_JOB_NAMES, JOB_DEPENDENCIES  # noqa: E402


EXPECTED_RECORD_TYPES = {
    "DailyBar", "IntradayQuote", "OrderBookSnapshot", "BrokerFlow",
    "ForeignFlow", "TradingStatus", "CorporateAction", "MarketIndex",
    "SymbolMetadata",
}
_BROKER_PRODUCTION_FILES = (
    "run_sde_job.py",
    "modules/job_runner/core.py",
    "modules/job_runner/enhanced_runtime_bridge.py",
    "modules/job_runner/enhanced_daily_reports.py",
    "modules/telegram/final_watchlist_chart.py",
)
_RETIRED_PRODUCTION_TOKENS = (
    "broker_multi_day",
    "broker_multiday",
    "broker_window_comparison",
    "broker_multiday_context",
    "broker_multiday_score",
    "multi_day_flow",
    "flow_persistence",
)


def _fail(msg: str) -> int:
    print(f"FAIL contract: {msg}")
    return 1


def check_record_types() -> int:
    missing = EXPECTED_RECORD_TYPES - set(RECORD_TYPES)
    if missing:
        return _fail(f"missing canonical record types: {sorted(missing)}")
    print(f"OK canonical: {len(EXPECTED_RECORD_TYPES)} record types registered")
    return 0


def check_broker_period_production_contract() -> int:
    """Keep retired broker-history interpretation outside Final Watchlist code."""
    violations: list[str] = []
    for relative_path in _BROKER_PRODUCTION_FILES:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        lowered = source.lower()
        for token in _RETIRED_PRODUCTION_TOKENS:
            if token in lowered:
                violations.append(f"{relative_path}:{token}")
    if violations:
        return _fail("retired broker production reference(s): " + ", ".join(violations))

    if "broker_multi_day" in INTEGRATED_JOB_NAMES:
        return _fail("broker_multi_day remained in integrated job registry")
    if "broker_multi_day" in JOB_DEPENDENCIES:
        return _fail("broker_multi_day remained in integrated dependency graph")
    expected_final_dependencies = ("market_outlook", "post_market", "broker_summary")
    if JOB_DEPENDENCIES.get("final_watchlist") != expected_final_dependencies:
        return _fail(
            "final_watchlist dependencies must be "
            f"{expected_final_dependencies!r}, got {JOB_DEPENDENCIES.get('final_watchlist')!r}"
        )

    period_view = (ROOT / "modules/broker_bridge/broker_period_view.py").read_text(encoding="utf-8").lower()
    for forbidden in ("broker_history", "broker_window_comparison", "broker_multiday"):
        if forbidden in period_view:
            return _fail(f"broker_period_view consulted retired source: {forbidden}")
    print("OK broker period: exact PRIMARY/TODAY only; no multi-day production route")
    return 0


def check_zapi_not_live_without_credentials() -> int:
    cfg_path = ROOT / "config" / "data_sources.json"
    if not cfg_path.exists():
        return _fail("config/data_sources.json missing")
    cfg = load_data_source_config(cfg_path)
    zapi = cfg.source("ZAPI_IDX")
    if zapi is None:
        return _fail("ZAPI_IDX source missing from config")
    if not zapi.enabled or not zapi.documentation_configured:
        return _fail("ZAPI_IDX documented capabilities must remain enabled in config")
    capabilities = zapi.capabilities

    for record_type in ("DailyBar", "MarketIndex"):
        status = str((capabilities.get(record_type) or {}).get("status", "")).upper()
        if status != "DISABLED_IN_PRODUCTION":
            return _fail(f"ZAPI_IDX production-disabled capability drifted: {record_type}={status}")
    for record_type in ("SymbolMetadata", "TradingStatus"):
        if str((capabilities.get(record_type) or {}).get("status", "")).upper() != "SUPPORTED":
            return _fail(f"ZAPI_IDX capability missing SUPPORTED status: {record_type}")
    for record_type in ("IntradayQuote", "OrderBookSnapshot", "BrokerFlow", "CorporateAction"):
        status = str((capabilities.get(record_type) or {}).get("status", "")).upper()
        if status not in {"UNSUPPORTED", "NOT_CONFIGURED"}:
            return _fail(f"unverified ZAPI capability was enabled: {record_type}={status}")

    from modules.data_sources.zapi_idx_adapter import MockZapiTransport, ZapiIdxClient

    client = ZapiIdxClient.from_config(zapi)
    if not zapi.api_key() and not isinstance(client._transport, MockZapiTransport):
        return _fail("credential-less ZAPI_IDX unexpectedly selected LIVE transport")
    print("OK config: documented ZAPI_IDX capabilities and credential-safe transport")
    return 0


def check_integrated_runtime_contract() -> int:
    missing = set(INTEGRATED_JOB_NAMES) - set(JOB_DEPENDENCIES)
    if missing:
        return _fail(f"integrated job dependency graph missing: {sorted(missing)}")
    manager = DataSourceManager(ROOT / "config/data_sources.json", root=ROOT, mode="MOCK", force_mock=True)
    metadata = manager.provider_metadata(record_type="DailyBar")
    if metadata.get("data_source_mode") == "LIVE" or not metadata.get("mock_used"):
        return _fail("forced mock source was labelled LIVE or mock_used=false")
    candidate = CanonicalCandidate(
        symbol="BBCA", trade_date="2026-01-02", final_action="WAIT",
        source_provenance={"provider_status": "FILE"}, snapshot_ids={"technical": "S"},
    )
    if validate_candidate(candidate):
        return _fail("canonical candidate contract rejected valid provenance")
    if not FINAL_ACTIONS.issuperset({"BUY", "WATCH", "WAIT", "AVOID", "NO_DATA"}):
        return _fail("final action contract incomplete")
    print(f"OK runtime: {len(INTEGRATED_JOB_NAMES)} jobs, manager metadata, candidate provenance")
    return 0


def check_structure_and_security() -> int:
    required_docs = {
        "README.md", "ARCHITECTURE.md", "RUNTIME_JOBS.md", "DATA_SOURCES.md",
        "TELEGRAM_ROUTING.md", "CONFIGURATION.md", "MIGRATION_V1_6_TO_V1_7.md",
        "TROUBLESHOOTING.md", "LEGACY_FILE_MANIFEST.md", "BROKER_PERIOD_ARCHITECTURE.md",
        "archive/README.md",
    }
    missing_docs = [name for name in required_docs if not (ROOT / "docs" / name).exists()]
    if missing_docs:
        return _fail(f"required runtime docs missing: {missing_docs}")
    entrypoint = (ROOT / "run_sde_job.py").read_text(encoding="utf-8")
    legacy = (ROOT / "master_pipeline.py").read_text(encoding="utf-8")
    if "JOBS" not in entrypoint or "DEPRECATED_COMPATIBILITY_ENTRYPOINT" not in legacy:
        return _fail("official entry point/legacy marker missing")
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    forbidden = [
        line for line in result.stdout.splitlines()
        if line == ".env" or line.endswith("/.env") or "__pycache__" in line
        or line.endswith((".db", ".sqlite", ".sqlite3", ".pyc"))
    ]
    if forbidden:
        return _fail(f"runtime/secret artifacts tracked: {forbidden}")
    print("OK structure: docs, official entry point, and Git runtime-artifact policy")
    return 0


def main() -> int:
    rc = 0
    rc |= check_record_types()
    rc |= check_broker_period_production_contract()
    rc |= check_zapi_not_live_without_credentials()
    rc |= check_integrated_runtime_contract()
    rc |= check_structure_and_security()
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
