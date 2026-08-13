# SDE Stabilization — Audit Traceability Register

## Purpose

Cumulative tracking register for audit-driven work on
`audit/sde-stabilization`. It does **not** replace
`docs/SDE_AUDIT_BASELINE.md`; the immutable audited observation point remains:

`121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`

## Frozen operating contract

- Package/config: `1.7.0-multisource`
- Production profile: `MODERATE_BASELINE`
- Auto-entry: `false`
- Approved autonomous operation: **not enabled**
- Stabilization scope: P0/P1 only; P2 remains tracked separately

Commit ownership:

1. Commit 1 — audit baseline + quant freeze
2. Commit 2 — artifact integrity
3. Commit 3 — lifecycle consistency
4. Commit 4 — runtime/status/locking
5. Commit 5 — canonical data path
6. Commit 6 — CI/release cleanup + final re-audit evidence

## Status vocabulary

- `OPEN`
- `IMPLEMENTED / PENDING RE-AUDIT`
- `IMPLEMENTED / PENDING PHASE 2 RE-AUDIT`
- `CLOSED BY RE-AUDIT`
- `DEFERRED`
- `EVIDENCE UPDATE`
- `STALE TEST CONTRACT`
- `RESOLVED IN COMMIT 6`

## Master audit finding register

| ID | Priority | Audit finding | Owner | Final status | Evidence |
|---|---|---|---|---|---|
| AF-P0-001 | P0 | CI suite red | Commit 6 | **CLOSED BY RE-AUDIT** | Historical audit retained at 28 failed / 492 passed; audited-base re-characterization was 27 failed / 510 passed / 3 subtests. Commit 6 candidate full CI: **565 passed, 3 subtests passed, 0 failed** plus all release gates PASS. |
| AF-P0-002 | P0 | Shared `FINAL_DECISION_V2.csv` writer not fully serialized/locked | Commit 2 | **CLOSED BY RE-AUDIT** | Dedicated V2 writer lock, run-scoped artifact, atomic canonical publish, SHA equivalence and stale-sidecar rejection; full regression suite PASS. |
| AF-P1-001 | P1 | Canonical data layer is not production execution boundary | Commit 5 | **CLOSED BY RE-AUDIT** | Production technical wrapper materializes provider rows through `DataSourceManager.route` into run-scoped canonical DailyBar CSVs; full canonical/data-path tests and contract validator PASS. |
| AF-P1-002 | P1 | TP1/lifecycle semantics differ across Exit Engine, tracker, DB, shadow and backtest | Commit 3 | **CLOSED BY RE-AUDIT** | `SDE_SWING_LIFECYCLE_V1`: TP1 milestone/open, TP2 close, same-candle stop priority, max-hold and actual trigger entry; cross-path tests PASS. |
| AF-P1-003 | P1 | Resend/delivery can overwrite engine `*_latest.json` | Commit 4 | **CLOSED BY RE-AUDIT** | `SDE_RUNTIME_STATUS_V1`: engine and delivery channels separated; resend declares no engine mutation; full suite PASS. |
| AF-P1-004 | P1 | Interrupt can leave inconsistent terminal state such as FAILED + exit code 0 | Commit 4 | **CLOSED BY RE-AUDIT** | Interrupt terminalization, exit 130, traceback, FAILED/nonzero invariant and ownership-safe lock release all covered by passing regression tests. |
| AF-P1-005 | P1 | Conflicting market dates can select a winner with fail-closed false | Commit 5 | **CLOSED BY RE-AUDIT** | `ConflictResolver` returns no winner, `CONFLICT_FAIL_CLOSED`, `fail_closed=true`; conflict regressions PASS. |
| AF-P2-001 | P2 | Telegram idempotency check/write not transaction-locked | Phase 2 Process 2 | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** | SQLite `BEGIN IMMEDIATE` reservation state machine covers concurrency, retries, failures, stale leases, explicit force resend, legacy JSON migration, and attempt history. Guarantee is explicitly at-least-once with crash ambiguity. |
| AF-P2-002 | P2 | Hotfix workflow has `contents: write` and auto-push | Phase 2 Commit 1 | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** | Workflow converted to read-only validation with `contents: read`, non-persisted checkout credentials, no source/test rewrite, and no commit/push path. See `docs/SDE_PHASE2_COMMIT1_RELEASE_GOVERNANCE.md`. |
| AF-P2-003 | P2 | DB revision history not fully immutable | Phase 2 Process 2 | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** | Append-only `market_prices_daily_revisions`, update/delete rejection triggers, migration of current legacy rows, and backward-compatible current/latest projection with integrity regression. |

## Historical audit evidence retained

- Run ID: `SWING-20260812-223409-24a9`
- Technical date: `2026-08-12`
- Broker date: `2026-08-12`
- Broker coverage: `40/40 - 100%`
- Quality: `VALID_WITH_ZAPI_WARNING`
- Decisions: 40
- BUY ON TRIGGER: 11
- WATCH: 12
- AVOID: 17
- Auto-entry: `false`

Implementation commits and final re-audit do not rewrite these historical facts.

## Commit register

### Commit 1 — Audit baseline + quant freeze

Commit: `78231c9f624c287fe0bdc24e090bb5015accecbd`

Status: IMPLEMENTED / VERIFIED BY FINAL RE-AUDIT

Evidence: `config/audit_quant_freeze.json`, `tools/ci_validate_quant_freeze.py`,
`tests/test_audit_quant_freeze.py`, `docs/SDE_STABILIZATION_BASELINE.md`.

### Commit 2 — Artifact integrity

Commit: `07efef3accfc864406a7e7d18a97a10bd3a8ff3b`

Status: CLOSED BY RE-AUDIT

Evidence: `modules/broker_fusion/broker_fusion_publisher.py`,
`tests/test_artifact_integrity_v2.py`,
`docs/SDE_STABILIZATION_COMMIT2_ARTIFACT_INTEGRITY.md`.

### Commit 3 — Lifecycle consistency

Commit: `3713dae78cbd6b7b4c5dc94cc45d409a9671824f`

Status: CLOSED BY RE-AUDIT

Contract: `SDE_SWING_LIFECYCLE_V1`

Evidence: `modules/analytics/lifecycle_contract.py`, lifecycle facades,
`tests/test_lifecycle_contract_v1.py`,
`tests/test_lifecycle_commit3_verification.py`, and
`docs/SDE_STABILIZATION_COMMIT3_LIFECYCLE_CONSISTENCY.md`.

### Commit 4 — Runtime / status / locking

Commit: `1158828fd91a2f40d1814fef3112d360c2ba610e`

Status: CLOSED BY RE-AUDIT

Contract: `SDE_RUNTIME_STATUS_V1`

Evidence:

- `modules/job_runner/runtime.py`
- `modules/job_runner/runtime_baseline.py`
- `modules/runtime/status.py`
- `tests/test_runtime_status_commit4.py`
- `docs/SDE_STABILIZATION_COMMIT4_RUNTIME_STATUS_LOCKING.md`

Verified semantics:

- `<job>_latest.json` remains engine/job owned;
- resend/delivery uses a separate delivery channel;
- resend never writes engine latest;
- terminal FAILED cannot retain exit code zero;
- KeyboardInterrupt crossing the job lock writes terminal interrupt evidence;
- lock ownership is token/run/PID/host based and stale cleanup rechecks before unlink.

### Commit 5 — Canonical data path

Commit: `e1344f43a9248fc72b3673edf7dfa91463df6051`

Status: CLOSED BY RE-AUDIT

Contract: `SDE_CANONICAL_DAILY_HISTORY_V1`

Evidence:

- `modules/data_sources/legacy_daily_bar_adapter.py`
- `modules/data_sources/conflict_resolver.py`
- `modules/technical_feature_engine/post_market_validated_runner.py`
- `tests/test_multisource_conflict.py`
- `tests/test_canonical_data_path_commit5.py`
- `docs/SDE_STABILIZATION_COMMIT5_CANONICAL_DATA_PATH.md`

Verified semantics:

- Yahoo/historical is acquisition-only;
- production technical wrapper is the engine boundary;
- rows are mapped to canonical DailyBar through `DataSourceManager.route`;
- frozen Technical Feature Engine receives run-scoped canonical CSVs;
- source/canonical hashes and run lineage are persisted;
- rows after expected closed date are blocked;
- missing expected canonical date is excluded rather than substituted;
- market-date conflict returns no winner and fails closed.

### Commit 6 — CI / release cleanup + final re-audit

Commit: the commit containing this register, with
`e1344f43a9248fc72b3673edf7dfa91463df6051` as its direct parent.
The exact SHA is authoritative in Git and intentionally not self-embedded.

Status: **CLOSED BY RE-AUDIT**, contingent on the identical squashed tree passing
its final GitHub Actions run; that final run is the authoritative release check
attached to the Commit 6 SHA.

Candidate evidence before squash:

- GitHub Actions run `31666812332`
- job `94343092346`
- candidate tree head `5ddfcd09c221ae73d0e5ca72d3d0599a9f81372a`
- compile PASS
- quant freeze PASS
- full pytest: **565 passed, 3 subtests passed, 0 failed**
- `git diff --check` PASS
- runtime config gate PASS (`VALID_WITH_WARNINGS`)
- canonical + multi-day contract PASS
- source integrity PASS
- credential scan PASS

Detailed evidence:
`docs/SDE_STABILIZATION_COMMIT6_RELEASE_EVIDENCE.md`.

## New findings discovered during stabilization

| ID | Found during | Observation | Disposition | Final status |
|---|---|---|---|---|
| NF-C1-001 | Commit 1 | Historical test evidence 28/492 differs from audited-base Actions evidence 27/510/3 subtests | Commit 6 evidence | EVIDENCE UPDATE |
| NF-C1-002 | Commit 1 | `audit/**` excluded from CI push trigger | Commit 6 added `audit/**` push coverage | **RESOLVED IN COMMIT 6** |
| NF-C2-001 | Commit 2 | Generic `swing_utils.atomic_csv()` uses deterministic destination tmp | Phase 2 Process 2 | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** |
| NF-C2-002 | Commit 2 | Generic JSON writers outside V2 are not uniformly atomic | Phase 2 Process 2 | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** |
| NF-C3-001 | Commit 3 | DB/backtest used D7/reference shortcut rather than ordered actual-entry lifecycle | Commit 3 | CLOSED BY RE-AUDIT |
| NF-C3-002 | Commit 3 | Shadow inferred TP hit rates from final-outcome text | Commit 3 | CLOSED BY RE-AUDIT |
| NF-C3-003 | Commit 3 | Runtime broker/decision/EMA exits cannot be perfectly replayed without historical context | Post-stabilization | DEFERRED |
| NF-C3-004 | Commit 3 | Exit state lacked explicit TP1/trailing persistence | Commit 3 | CLOSED BY RE-AUDIT |
| NF-C3-005 | Commit 3 | Tracker post-entry evaluation could be truncated by trigger-expiry window | Commit 3 | CLOSED BY RE-AUDIT |
| NF-C3-006 | Commit 3 | Existing regression encoded TP1-as-full-close | Commit 6 stale-test cleanup | **RESOLVED IN COMMIT 6** |
| NF-C3-007 | Commit 3 verification | Same-session Exit Engine rerun could advance holding age twice | Commit 3 | CLOSED BY RE-AUDIT |
| NF-C3-008 | Commit 3 verification | Initial backtest compatibility facade could recurse | Commit 3 | CLOSED BY RE-AUDIT |
| NF-C4-001 | Commit 4 | Delivery-only resend reused engine status writer | Commit 4 | CLOSED BY RE-AUDIT |
| NF-C4-002 | Commit 4 | `KeyboardInterrupt` bypassed `except Exception` terminalization | Commit 4 | CLOSED BY RE-AUDIT |
| NF-C4-003 | Commit 4 | Status writer accepted terminal FAILED with exit code 0 | Commit 4 | CLOSED BY RE-AUDIT |
| NF-C4-004 | Commit 4 | Lock release unlinked by path without owner verification | Commit 4 | CLOSED BY RE-AUDIT |
| NF-C4-005 | Commit 4 | Stale-lock PID liveness was checked without host ownership | Commit 4 | CLOSED BY RE-AUDIT |
| NF-C4-006 | Commit 4 | Some legacy exception branches use repository-default traceback path rather than `ctx.status_root` | Phase 2 Process 2 | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** |
| NF-C5-001 | Commit 5 | Production wrapper still hardlinked/copied raw provider CSVs into TFE input | Commit 5 canonicalization | CLOSED BY RE-AUDIT |
| NF-C5-002 | Commit 5 | Market-date mismatch selected source-priority winner with `fail_closed=false` | Commit 5 | CLOSED BY RE-AUDIT |
| NF-C5-003 | Commit 5 | `HISTORICAL_PROVIDER` note says “Fallback only” although DailyBar ownership says primary | Post-stabilization | DEFERRED |
| NF-C5-004 | Commit 5 | Generic canonical quality engine lacks configured BEI holiday injection | Phase 2 Process 2 | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** |
| NF-C6-001 | Commit 6 | Stabilization compatibility facades could recurse/bypass baseline delegates | Commit 6 facade repair | **RESOLVED IN COMMIT 6** |
| NF-C6-002 | Commit 6 | Multiple legacy regressions encoded superseded lifecycle/canonical/presentation contracts | Commit 6 test-contract cleanup | **RESOLVED IN COMMIT 6** |
| NF-C6-003 | Commit 6 | Post Market has diagnostic and market-first contracts that must remain distinct | Explicit payload/version contract | **RESOLVED IN COMMIT 6** |
| NF-C6-004 | Commit 6 | Generic REPORT ownership was ambiguous between router and scheduler | Router isolation + scheduler fallback + definitive validation | **RESOLVED IN COMMIT 6** |
| NF-C6-005 | Commit 6 | Same-session Yahoo regression contradicted post-close revalidation safety contract | Regression aligned to existing revalidation behavior | **RESOLVED IN COMMIT 6** |
| P2P2-NF-001 | Phase 2 Process 2 | Concurrent Windows `os.replace` can transiently fail with a destination sharing conflict | Process 2 bounded replace retry + concurrency regression | **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT** |

Detailed non-blocking observations remain in
`docs/SDE_STABILIZATION_DEFERRED_FINDINGS.md`.

## Phase 2 hardening commit register

### Phase 2 Commit 1 - Release governance

Finding: `AF-P2-002`

Direct parent: `662c5ef160c7978237abc58e73e897190daf0632`

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

The exact commit SHA is authoritative in Git and is intentionally not embedded
in its own documentation. The implementation removes automated source/test
rewrites, bot commit creation, and direct branch push from the legacy hotfix
workflow. Validation remains available with read-only repository permission and
non-persisted checkout credentials.

Evidence:

- `.github/workflows/hotfix-exit-volume.yml`
- `tests/test_release_governance_workflow.py`
- `docs/SDE_PHASE2_COMMIT1_RELEASE_GOVERNANCE.md`

No production application, quant, lifecycle, runtime-status, canonical-data, or
V2 publication behavior is owned by this Phase 2 commit.

### Phase 2 Process 2 - Runtime and data integrity

Findings: `AF-P2-001`, `AF-P2-003`, `NF-C2-001`, `NF-C2-002`,
`NF-C4-006`, and `NF-C5-004`.

Direct parent: `de8bafe7485c4926bbc85b8575e8ac245934bf89`

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

The exact final commit SHA is authoritative in Git and is intentionally not
embedded in its own documentation. Process 2 adds transactional Telegram
reservations, append-only historical price revisions, generic artifact
durability, context-owned traceback routing, and record-type-aware BEI calendar
validation. It also records and fixes in-scope finding `P2P2-NF-001`.

Evidence:

- `docs/SDE_PHASE2_PROCESS2_RUNTIME_DATA_INTEGRITY.md`
- `tests/test_delivery_idempotency_phase2.py`
- `tests/test_historical_price_revisions_phase2.py`
- `tests/test_artifact_durability_phase2.py`
- `tests/test_traceback_paths_phase2.py`
- `tests/test_bei_calendar_injection_phase2.py`

Final local evidence: compile PASS; Process 2 targeted aggregate **176 passed**;
full suite **588 passed, 2 failed, 3 subtests passed**. The two failures are
exactly the known Process 3 Windows portability findings `PA2-NF-001` and
`PA2-NF-002`. Quant/release validators reproduce only `PA2-NF-001`; protected
HEAD blobs remain identical to the freeze baseline.

Quant, scoring, blocker, candidate-selection, entry, SL, TP, RR, and lifecycle
semantics are not owned by this process. `auto_entry_enabled=false` remains
frozen.

## Guardrail traceability

| Guardrail | Final re-audit state |
|---|---|
| `auto_entry_enabled=false` | FROZEN / PASS |
| `MODERATE_BASELINE` | FROZEN / PASS |
| Scoring/weights/thresholds | FROZEN / PASS |
| Hard blockers | FROZEN / PASS |
| Entry-zone calculation | FROZEN / PASS |
| Initial SL calculation | FROZEN / PASS |
| TP1/TP2 price calculation | FROZEN / PASS |
| Closed-candle policy | FROZEN / PASS |
| Protected Technical Feature Engine | Quant/source freeze validator PASS |
| Shared V2 publication | Serialized/atomic/hash-consistent regressions PASS |
| Broker date/coverage validation | PASS |
| Data-quality propagation | PASS |
| Config hash/run manifest | PASS |
| SQLite integrity/WAL/FK checks | Regression suite PASS |
| Lifecycle event idempotency | PASS |
| Shadow-only profile comparison | PASS |

## Release-gate result

Commit 6 evidence demonstrates:

- no required-test failures;
- quant freeze passes;
- canonical/data-path contracts pass;
- conflict dates fail closed with no winner;
- V2 publication integrity regressions pass;
- resend/engine status separation regressions pass;
- interrupt and lock-owner regressions pass;
- lifecycle semantics pass across owned evaluation paths;
- auto-entry remains false;
- audit branch now receives direct-push CI coverage;
- source-integrity and credential scans pass.

CI deliberately runs without live provider or Telegram credentials. Therefore
this re-audit is deterministic code/config/contract evidence, not proof that a
real provider or Telegram endpoint was reachable during CI.

## Final re-audit closure

**P0/P1 stabilization: GREEN / PASS.**

All original P0/P1 findings are closed by re-audit evidence. Original P2
findings and explicitly deferred new findings remain open for consolidated
post-stabilization discussion.

The historical `64/100 — AMBER/RED` audit verdict remains an immutable baseline
record. It is not overwritten by this register. The new conclusion is that the
agreed P0/P1 stabilization gate has passed while `auto_entry_enabled=false`
continues to prohibit autonomous order entry.
