# Stage 3 Multi-Source Architecture Report
**Date:** 2026-08-03  
**Branch:** agent/sde-swing-v1-7-0-multisource  
**Baseline:** agent/sde-swing-v1-6-2-stage1-stabilized

---

## 1. Scope

Stage 3 adds a production-grade multi-source market data layer and a broker multi-day context engine to SDE Swing V1.6.x. It does **not** change any Stage 2 trading thresholds, does not enable auto-entry, and does not merge to `main`.

---

## 2. Architecture Overview

```
External Sources
  ZAPI IDX (NOT_CONFIGURED)   Stockbit (broker raw)   Historical Provider
       │                            │                        │
  ZapiIdxAdapter            StockbitAdapter          (future adapter)
       │                            │                        │
       └──────────── SourceRouter ──────────────────────────┘
                          │
              DataQualityEngine  ←→  ConflictResolver
                          │
                   CanonicalRecord (provenance envelope)
                          │
              ┌───────────┴───────────┐
         FeatureEngine          BrokerMultiDayEngine
                                      │
                              DecisionBridge (context only)
                                      │
                          Decision Engine (unchanged owner of Decision_Status_Final)
```

---

## 3. New Modules (17 files in `modules/data_sources/`: 16 modules + `__init__.py`)

| Module | Section | Purpose |
|---|---|---|
| `constants.py` | B | Shared string constants, WIB timezone, reason codes |
| `canonical.py` | C | 9 typed canonical record dataclasses + provenance envelope |
| `config.py` | D | Source config loader (separate from pipeline.json) |
| `base.py` | B | Transport/SourceClient/Adapter ABCs, error hierarchy |
| `data_quality.py` | G | 15 reason codes, hard-reject set, QualityResult |
| `conflict_resolver.py` | H | Per-field resolution, 4 modes, corporate-action hook |
| `router.py` | B | SourceRouter: validates → resolves → re-validates winner |
| `zapi_idx_adapter.py` | E | ZAPI adapter (NOT_CONFIGURED; MockZapiTransport for CI) |
| `stockbit_adapter.py` | F | Wraps Stage-1 broker parser → canonical BrokerFlow |
| `health.py` | I | SourceHealthMonitor: telemetry, health_status property |
| `broker_history.py` | J | SQLite WAL, upsert, retention, window loader |
| `broker_windows.py` | K–L | 5 windows (1D/3D/5D/10D/20D), WindowFeatures, weighted cost |
| `broker_analytics.py` | M–R | Classification (9 classes), persistence, acceleration, divergence, alignment, foreign double-count |
| `broker_multiday_engine.py` | J–R | MultiDayContext orchestrator — context only, never BUY/WATCH/AVOID |
| `decision_bridge.py` | S–T | attach_multiday_context (protected-column guard), shadow comparison |
| `broker_multiday_output.py` | U | 5 CSVs + manifest + Telegram summary |

---

## 4. New Config / Env Files

- `config/data_sources.json` — source ownership, resolver mode, ZAPI disabled by default
- `.env.example` — blank credential placeholders (no secrets)

---

## 5. Hard Constraints — Verified

| Constraint | Status |
|---|---|
| `main` not changed | ✅ All work on `agent/sde-swing-v1-7-0-multisource` |
| No merge to main | ✅ |
| auto_entry_enabled = false | ✅ Enforced by runtime_config validator + CI check |
| Stage 2 thresholds unchanged | ✅ No edits to pipeline.json decision weights/thresholds |
| Multi-day engine: context only | ✅ No BUY/WATCH/AVOID in any output; CI contract check enforces this |
| Decision_Status_Final protected | ✅ attach_multiday_context() verifies byte-identity before/after and restores on change |
| ZAPI NOT_CONFIGURED | ✅ enabled=false, documentation_configured=false; CI checks this |
| API keys from env only | ✅ SourceConfig.api_key() reads os.environ; never from JSON |
| No live API in CI | ✅ All env vars blank in ci.yml; ZapiIdxClient falls back to MockZapiTransport |
| No secrets committed | ✅ .gitignore excludes .env, *.db, data/output/, logs/ |

---

## 6. Canonical Schema

Every record carries a **provenance envelope** (12 fields):

```
symbol, market_date, event_timestamp, received_at, source,
source_record_id, freshness_seconds, quality_status, quality_reasons,
fallback_used, conflict_status, raw_payload_hash, schema_version,
field_provenance
```

9 record types registered: `DailyBar`, `IntradayQuote`, `OrderBookSnapshot`, `BrokerFlow`, `ForeignFlow`, `TradingStatus`, `CorporateAction`, `MarketIndex`, `SymbolMetadata`.

---

## 7. Data Quality Engine

15 reason codes. Hard-reject set (fail-closed):

```
INVALID_SCHEMA, MISSING_REQUIRED_FIELD, FUTURE_DATED_DATA,
WRONG_MARKET_DATE, NON_TRADING_DAY, INVALID_OHLC, NEGATIVE_VOLUME,
SUSPEND_STATUS_AMBIGUOUS
```

Warning-only (accepted with flag): `STALE_DATA`, `DUPLICATE_RECORD`, `TIMEZONE_MISMATCH`, `PARTIAL_DAILY_CANDLE`, `INSUFFICIENT_MICROSTRUCTURE_DATA`, `SOURCE_CONFLICT`, `SYMBOL_MISMATCH`.

---

## 8. Conflict Resolver

4 modes: `PRIMARY_ONLY`, `PRIMARY_WITH_FALLBACK` (default), `CONSENSUS`, `SHADOW_COMPARE`.

Key rules:
- Different market dates → never merged (`CONFLICT_UNRESOLVED`)
- TradingStatus disagreement → fail-closed (`CONFLICT_FAIL_CLOSED`)
- Corporate action hook checked before OHLC conflict declared
- CONSENSUS mode: median of numeric candidates

---

## 9. Broker Multi-Day Engine

**Contract (enforced by CI):** produces only `Broker_MultiDay_Score`, `Broker_MultiDay_Confidence`, `Broker_MultiDay_Penalty`, `Broker_MultiDay_Blocker`, `Broker_MultiDay_Context`, `Broker_MultiDay_Trace`. Never BUY/WATCH/AVOID.

Windows: 1D, 3D, 5D (default primary), 10D, 20D.

9 classification labels: `STRONG_ACCUMULATION`, `ACCUMULATION`, `EARLY_ACCUMULATION`, `NEUTRAL`, `MIXED`, `DIVERGENCE`, `DISTRIBUTION`, `STRONG_DISTRIBUTION`, `INSUFFICIENT_DATA`.

`STRONG_DISTRIBUTION` is a hard blocker. Single-day domination (≥60% of window flow in one session) applies a 20-point penalty and reduces score by 40%.

Weighted broker cost: value/volume-weighted (never simple mean of prices).

Foreign double-count protection: `flow_origin="DERIVED_FROM_BROKER"` + aggregate feed present → `double_count_risk=True`, use only one source.

---

## 10. Decision Bridge

`attach_multiday_context()`:
- Left-joins 15 context columns onto decision frame
- Verifies `Decision_Status_Final`, `Decision_Status`, `Decision_Status_PrePlan`, `Final_Score_V3`, `Decision_V3` are byte-identical before and after
- Restores any changed protected column and sets `protected_intact=False`

Shadow comparison (`run_shadow_comparison()`): evaluates 6 window framings side-by-side on the same inputs. Reports context distribution only — never used to pick the framing that produces the most BUYs.

---

## 11. Output Files

| File | Description |
|---|---|
| `BROKER_MULTIDAY_DETAIL.csv` | Full context bundle per symbol |
| `BROKER_MULTIDAY_SUMMARY.csv` | Compact score/confidence/blocker per symbol |
| `BROKER_ROTATION.csv` | Persistence and rotation metrics |
| `BROKER_DIVERGENCE.csv` | Broker-price divergence labels |
| `BROKER_WINDOW_COMPARISON.csv` | Per-symbol per-window classification |
| `BROKER_MULTIDAY_MANIFEST.json` | Run metadata + contract assertion |

---

## 12. CI Updates

New steps added to `.github/workflows/ci.yml`:
- `tools/ci_validate_runtime_config.py` — pipeline.json strict validation + auto_entry=false
- `tools/ci_validate_contracts.py` — canonical types, multi-day context-only, bridge protection, ZAPI NOT_CONFIGURED
- Credential scan (private key patterns, AWS key patterns, Slack tokens)
- `.env` committed check

All env vars set to blank in CI — no live API call possible.

---

## 13. Test Coverage (Section V)

New test files:

| File | Tests |
|---|---|
| `test_multisource_schema.py` | Provenance envelope, 9 record types, closed/partial candle, WIB freshness, field provenance, payload hash, config loads, api_key from env only |
| `test_multisource_quality.py` | Reason-code scenarios: valid-accepted, future data, missing field, invalid OHLC, negative volume, non-trading day, wrong date, stale, duplicate, timezone, partial candle, suspend ambiguity, insufficient microstructure |
| `test_multisource_conflict.py` | Single source, within tolerance, field-level resolution, market date mismatch, suspend fail-closed, corporate action, consensus median |
| `test_multisource_zapi_mock.py` | NOT_CONFIGURED → mock, force_mock, disabled raises SourceNotConfigured, DailyBar/TradingStatus mapping, mock never makes HTTP |
| `test_multisource_router.py` | Primary selected, fallback on missing, fallback on invalid primary, PRIMARY_ONLY no fallback, no candidates, status ambiguity fail-closed, source not leaked to domain |
| `test_multisource_health.py` | NOT_CONFIGURED, healthy, unavailable (no success / high failure rate), degraded (fallback / validation), write snapshot |
| `test_multisource_broker_multiday.py` | 1D/3D/5D/10D/20D windows, incomplete→INSUFFICIENT_DATA, duplicate day, weighted cost (not simple mean), no avg_price→None, persistence stable dominance, new buyers counted, acceleration (all 4 states), acceleration across windows, single-day domination penalty, STRONG_DISTRIBUTION blocker, divergence (confirmed accumulation/distribution, no price, incomplete window), alignment (fully aligned, short-term confirming, context fields), foreign double-count (no aggregate, derived+aggregate→risk, aggregate feed→no risk, at-risk uses broker-derived) |

---

## 14. Files NOT Changed

- `config/pipeline.json` — production profile, shadow profiles, thresholds, auto_entry all unchanged
- `modules/runtime_config.py` — strict validator unchanged
- `master_pipeline.py` — unchanged
- All Stage 1/2 modules — unchanged
- All 129 legacy regression tests — must continue to pass

---

## 15. Commit

```
feat: add Stage 3 multi-source and broker multi-day architecture
```

Push target: `agent/sde-swing-v1-7-0-multisource` only. Do NOT merge to `main`.
