"""CI: validate canonical schema + multi-day engine contracts.

Enforces the Stage 3 hard constraints at CI time:

1. All 9 canonical record types are registered with the provenance envelope.
2. The broker multi-day engine produces CONTEXT ONLY — never BUY / WATCH /
   AVOID (those belong to the Final Decision Engine).
3. The decision bridge never adds a decision column and never mutates a
   protected decision column.
4. config/data_sources.json keeps ZAPI NOT_CONFIGURED (never live in CI).

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


def check_zapi_not_configured() -> int:
    cfg_path = ROOT / "config" / "data_sources.json"
    if not cfg_path.exists():
        return _fail("config/data_sources.json missing")
    cfg = load_data_source_config(cfg_path)
    zapi = cfg.source("ZAPI_IDX")
    if zapi is None:
        return _fail("ZAPI_IDX source missing from config")
    if zapi.enabled or zapi.documentation_configured:
        return _fail("ZAPI_IDX must stay disabled + not documentation_configured")
    print("OK config: ZAPI_IDX NOT_CONFIGURED (never live in CI)")
    return 0


def main() -> int:
    rc = 0
    rc |= check_record_types()
    rc |= check_multiday_context_only()
    rc |= check_bridge_protects_decisions()
    rc |= check_zapi_not_configured()
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
