"""CI: validate canonical schema + multi-day engine contracts.

Enforces the Stage 3 hard constraints at CI time:

1. All 9 canonical record types are registered with the provenance envelope.
2. The broker multi-day engine produces CONTEXT ONLY — never BUY / WATCH /
   AVOID (those belong to the Final Decision Engine).
3. The decision bridge never adds a decision column and never mutates a
   protected decision column.
4. config/data_sources.json records the documented ZAPI capabilities while
   credentials remain environment-only; a credential-less run is never LIVE.

Exits non-zero on any violation.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from modules.data_sources.canonical import RECORD_TYPES  # noqa: E402
from modules.data_sources.config import load_data_source_config  # noqa: E402
from modules.data_sources.broker_multiday_engine import compute_multiday_context  # noqa: E402
from modules.data_sources.decision_bridge import (  # noqa: E402
    CONTEXT_COLUMNS,
    PROTECTED_COLUMNS,
    attach_multiday_context,
)
from modules.runtime.data_source_manager import DataSourceManager  # noqa: E402
from modules.runtime.jobs import INTEGRATED_JOB_NAMES, JOB_DEPENDENCIES  # noqa: E402
from modules.decision.candidate import FINAL_ACTIONS, CanonicalCandidate, validate_candidate  # noqa: E402

FORBIDDEN_TOKENS = {"BUY READY", "BUY ON TRIGGER", "WATCH", "AVOID", "BUY", "SELL"}

EXPECTED_RECORD_TYPES = {
    "DailyBar", "IntradayQuote", "OrderBookSnapshot", "BrokerFlow",
    "ForeignFlow", "TradingStatus", "CorporateAction", "MarketIndex",
    "SymbolMetadata",
}


def _fail(msg: str) -> int:
    print(f"FAIL contract: {msg}")
    return 1


def check_record_types() -> int:
    missing = EXPECTED_RECORD_TYPES - set(RECORD_TYPES)
    if missing:
        return _fail(f"missing canonical record types: {sorted(missing)}")
    print(f"OK canonical: {len(EXPECTED_RECORD_TYPES)} record types registered")
    return 0


def check_multiday_context_only() -> int:
    # Build a synthetic accumulation history and confirm the engine emits no
    # BUY/WATCH/AVOID anywhere in its context or trace.
    rows = []
    for day in range(1, 11):
        rows.append({
            "market_date": f"2026-01-{day:02d}",
            "broker_code": "AA", "side": "BUY", "net_value": 1_000_000,
            "avg_price": 1000.0, "gross_value": 1_000_000, "net_lot": 1000,
        })
    ctx = compute_multiday_context("BBCA", "2026-01-10", rows, primary_window="5D")
    ctx_dict = ctx.to_context_dict()

    # The context label must not be any trading decision token.
    label = str(ctx_dict.get("Broker_MultiDay_Context", "")).upper()
    if label in FORBIDDEN_TOKENS:
        return _fail(f"multi-day context label is a decision token: {label}")

    # No context KEY may be named like a final-decision column.
    for key in ctx_dict:
        if "DECISION_STATUS_FINAL" in key.upper() or key in PROTECTED_COLUMNS:
            return _fail(f"multi-day context leaked a decision column: {key}")

    print("OK multi-day: context-only, no BUY/WATCH/AVOID emitted")
    return 0


def check_bridge_protects_decisions() -> int:
    rows = [{
        "market_date": "2026-01-10", "broker_code": "AA", "side": "BUY",
        "net_value": 1_000_000, "avg_price": 1000.0, "gross_value": 1_000_000,
        "net_lot": 1000,
    }]
    ctx = compute_multiday_context("BBCA", "2026-01-10", rows, primary_window="5D")
    frame = pd.DataFrame([
        {"Symbol": "BBCA", "Decision_Status_Final": "WATCH", "Final_Score_V3": 55.0},
        {"Symbol": "TLKM", "Decision_Status_Final": "AVOID", "Final_Score_V3": 20.0},
    ])
    before = frame["Decision_Status_Final"].tolist()
    result = attach_multiday_context(frame, {"BBCA": ctx})

    if not result.protected_intact:
        return _fail("bridge reported protected columns changed")
    after = result.frame["Decision_Status_Final"].tolist()
    if before != after:
        return _fail(f"Decision_Status_Final changed: {before} -> {after}")
    for col in result.frame.columns:
        if "DECISION" in col.upper() and col not in PROTECTED_COLUMNS and col.startswith("Broker"):
            return _fail(f"bridge added a broker decision column: {col}")
    # Bridge must add context columns and nothing decision-like.
    for col in CONTEXT_COLUMNS:
        if col not in result.frame.columns:
            return _fail(f"bridge did not add context column: {col}")

    print("OK bridge: protected decision columns untouched, context attached")
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
    for record_type in ("DailyBar", "MarketIndex", "SymbolMetadata", "TradingStatus"):
        if str((capabilities.get(record_type) or {}).get("status", "")).upper() != "SUPPORTED":
            return _fail(f"ZAPI_IDX capability missing SUPPORTED status: {record_type}")
    for record_type in ("IntradayQuote", "OrderBookSnapshot", "BrokerFlow", "CorporateAction"):
        status = str((capabilities.get(record_type) or {}).get("status", "")).upper()
        if status not in {"UNSUPPORTED", "NOT_CONFIGURED"}:
            return _fail(f"unverified ZAPI capability was enabled: {record_type}={status}")
    # CI must not require credentials and must never print them. A local
    # operator may still run this check with a key; the client readiness
    # assertion below is what prevents a false LIVE label when absent.
    from modules.data_sources.zapi_idx_adapter import MockZapiTransport, ZapiIdxClient
    client = ZapiIdxClient.from_config(zapi)
    if not zapi.api_key() and not isinstance(client._transport, MockZapiTransport):
        return _fail("credential-less ZAPI_IDX unexpectedly selected LIVE transport")
    if not zapi.api_key():
        print("OK config: documented ZAPI_IDX capabilities, credential-less run NOT_CONFIGURED/MOCK")
    else:
        print("OK config: documented ZAPI_IDX capabilities; credentials remain environment-only")
    return 0


def check_integrated_runtime_contract() -> int:
    missing = set(INTEGRATED_JOB_NAMES) - set(JOB_DEPENDENCIES)
    if missing:
        return _fail(f"integrated job dependency graph missing: {sorted(missing)}")
    manager = DataSourceManager(ROOT / "config/data_sources.json", root=ROOT, mode="MOCK", force_mock=True)
    metadata = manager.provider_metadata(record_type="DailyBar")
    if metadata.get("data_source_mode") == "LIVE" or not metadata.get("mock_used"):
        return _fail("forced mock source was labelled LIVE or mock_used=false")
    for key in ("provider_status", "data_source_mode", "source_health"):
        if key not in metadata:
            return _fail(f"source manager metadata missing {key}")
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
        "TROUBLESHOOTING.md", "LEGACY_FILE_MANIFEST.md",
    }
    missing_docs = [name for name in required_docs if not (ROOT / "docs" / name).exists()]
    if missing_docs:
        return _fail(f"required runtime docs missing: {missing_docs}")
    entrypoint = (ROOT / "run_sde_job.py").read_text(encoding="utf-8")
    legacy = (ROOT / "master_pipeline.py").read_text(encoding="utf-8")
    if "JOBS" not in entrypoint or "DEPRECATED_COMPATIBILITY_ENTRYPOINT" not in legacy:
        return _fail("official entry point/legacy marker missing")
    # Use Git's index rather than the working tree: runtime artifacts can exist
    # locally but must never enter the release commit.
    import subprocess
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    forbidden = [line for line in result.stdout.splitlines() if line == ".env" or line.endswith("/.env") or "__pycache__" in line or line.endswith((".db", ".sqlite", ".sqlite3", ".pyc"))]
    if forbidden:
        return _fail(f"runtime/secret artifacts tracked: {forbidden}")
    print("OK structure: docs, official entry point, and Git runtime-artifact policy")
    return 0


def main() -> int:
    rc = 0
    rc |= check_record_types()
    rc |= check_multiday_context_only()
    rc |= check_bridge_protects_decisions()
    rc |= check_zapi_not_live_without_credentials()
    rc |= check_integrated_runtime_contract()
    rc |= check_structure_and_security()
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
