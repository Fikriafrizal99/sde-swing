# SDE Stabilization — Audit Traceability Register

## Purpose

This is the cumulative tracking register for audit-driven work on
`audit/sde-stabilization`.

It does **not** replace `docs/SDE_AUDIT_BASELINE.md`. The audit baseline remains
the immutable statement of observations at audited commit
`121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`.

This register answers what every finding is, which commit owns it, what evidence
supports the implementation, what new findings appeared, and what still needs
final re-audit.

## Branch and frozen operating contract

- Audited baseline: `121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`
- Branch: `audit/sde-stabilization`
- Package/config: `1.7.0-multisource`
- Production profile: `MODERATE_BASELINE`
- Auto-entry: `false`
- Approved operation until re-audit: supervised/shadow only

Planned ownership:

1. Commit 1 — audit baseline + quant freeze
2. Commit 2 — artifact integrity
3. Commit 3 — lifecycle consistency
4. Commit 4 — runtime/status/locking
5. Commit 5 — canonical data path
6. Commit 6 — CI/release cleanup + final re-audit evidence

## Status vocabulary

- `OPEN` — not implemented yet.
- `IMPLEMENTED / PENDING RE-AUDIT` — scoped implementation exists, but closure
  has not yet been proven by final audit evidence.
- `CLOSED BY RE-AUDIT` — final audit confirmed closure.
- `DEFERRED` — intentionally outside current P0/P1 stabilization scope.
- `EVIDENCE UPDATE` — evidence was corrected without changing behavior.
- `STALE TEST CONTRACT` — a test encodes behavior explicitly identified as an
  audit defect and must be rewritten rather than forcing production backward.

## Master audit finding register

| ID | Priority | Audit finding | Baseline classification | Owner | Current status | Evidence / next proof |
|---|---|---|---|---|---|---|
| AF-P0-001 | P0 | CI suite red | CONFIRMED ISSUE | Commit 6 | OPEN | Historical audit: 28 failed / 492 passed. Commit 1 current baseline evidence: 27 failed / 510 passed / 3 subtests passed; compile PASS. Final full-suite closure required. |
| AF-P0-002 | P0 | Shared `FINAL_DECISION_V2.csv` writer not fully serialized/locked; overwrite race possible | CONFIRMED ISSUE; race POTENTIAL RISK | Commit 2 | IMPLEMENTED / PENDING RE-AUDIT | Dedicated V2 writer lock, run-scoped artifact, atomic canonical publish, SHA equivalence, stale sidecar rejection. |
| AF-P1-001 | P1 | Canonical data layer is not the production execution boundary | CONFIRMED ISSUE | Commit 5 | OPEN | DataSourceManager exists, but audited Stage 1/2 still use legacy execution subprocess paths. |
| AF-P1-002 | P1 | TP1/lifecycle semantics differ across Exit Engine, outcome tracker, DB, shadow and backtest | CONFIRMED ISSUE | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT | `SDE_SWING_LIFECYCLE_V1` added. TP1 milestone/open, TP2 full close, stop same-candle priority, max-hold and actual trigger entry unified for canonical plan-backed paths. Final runtime/re-audit proof still required. |
| AF-P1-003 | P1 | Resend/delivery can overwrite engine `*_latest.json` status | CONFIRMED ISSUE | Commit 4 | OPEN | Engine/job status and delivery/resend status ownership must be separated. |
| AF-P1-004 | P1 | Interrupt can leave inconsistent terminal state such as FAILED + exit code 0 | CONFIRMED ISSUE | Commit 4 | OPEN | Terminalization, nonzero exit, finished_at, traceback/error evidence and lock cleanup must be consistent. |
| AF-P1-005 | P1 | Conflicting market dates can select a winner with fail-closed false | CONFIRMED ISSUE | Commit 5 | OPEN | Canonical date conflict must block and propagate invalid quality. |
| AF-P2-001 | P2 | Telegram idempotency check/write not transaction-locked | POTENTIAL RISK | Post-stabilization | DEFERRED | Explicitly out of P0/P1 scope. |
| AF-P2-002 | P2 | Hotfix workflow has `contents: write` and auto-push | CONFIRMED ISSUE | Post-stabilization | DEFERRED | Governance/security follow-up after stabilization. |
| AF-P2-003 | P2 | DB revision history not fully immutable | POTENTIAL RISK | Post-stabilization | DEFERRED | No full DB revision redesign inside Commit 3/4. |

## Baseline evidence retained

Validated audit run remains historical baseline evidence:

- Run ID: `SWING-20260812-223409-24a9`
- Technical date: `2026-08-12`
- Broker date: `2026-08-12`
- Broker coverage: `40/40 - 100%`
- Quality: `VALID_WITH_ZAPI_WARNING`
- Decision rows: `40`
- BUY ON TRIGGER: `11`
- WATCH: `12`
- AVOID: `17`
- Auto-entry: `false`

Implementation work must not rewrite these historical facts.

## Commit register

### Commit 1 — Audit baseline + quant freeze

Commit: `78231c9f624c287fe0bdc24e090bb5015accecbd`

Status: IMPLEMENTED

Evidence:

- `config/audit_quant_freeze.json`
- `tools/ci_validate_quant_freeze.py`
- `tests/test_audit_quant_freeze.py`
- `docs/SDE_STABILIZATION_BASELINE.md`

Protected contract includes `MODERATE_BASELINE`, weights/scoring, thresholds,
hard blockers, closed-candle policy, entry/SL/TP price calculation, risk/RR
parameters, and `auto_entry_enabled=false`.

### Commit 2 — Artifact integrity

Commit: `07efef3accfc864406a7e7d18a97a10bd3a8ff3b`

Status: IMPLEMENTED / PENDING RE-AUDIT

Evidence:

- `modules/broker_fusion/broker_fusion_publisher.py`
- `tests/test_artifact_integrity_v2.py`
- `docs/SDE_STABILIZATION_COMMIT2_ARTIFACT_INTEGRITY.md`

Quant owner `modules/broker_fusion/broker_fusion.py` remains unchanged.

### Commit 3 — Lifecycle consistency

Commit: this commit; final SHA is the parent of Commit 4 and will be pinned at
final re-audit.

Status: IMPLEMENTED / PENDING RE-AUDIT

Canonical contract: `SDE_SWING_LIFECYCLE_V1`

Evidence:

- `modules/analytics/lifecycle_contract.py`
- `modules/exit_engine/exit_engine.py` facade + `exit_engine_baseline.py`
- `modules/analytics/outcome_tracker.py` facade + `outcome_tracker_baseline.py`
- `modules/database/swing_history_db.py` facade + `swing_history_db_baseline.py`
- `modules/backtesting/backtest_engine.py` facade + `backtest_engine_baseline.py`
- `modules/analytics/profile_shadow.py` facade + `profile_shadow_baseline.py`
- `tests/test_lifecycle_contract_v1.py`
- `tests/test_lifecycle_commit3_verification.py`
- `docs/SDE_STABILIZATION_COMMIT3_LIFECYCLE_CONSISTENCY.md`

Canonical semantics:

- TP1 = milestone + trailing-active state, not full close;
- TP2 = full close;
- stop has conservative priority if stop and target coexist in one daily bar;
- new BE/trailing stop calculated from close applies from the next candle;
- max-hold counts executable holding sessions and same-session reruns do not
  advance holding age twice;
- plan-backed evaluation uses actual trigger entry;
- entered-trade final outcome = WIN / LOSS / AMBIGUOUS / OPEN;
- entry/initial-stop/TP1/TP2 prices are not recalculated by Commit 3.

## New findings discovered during stabilization

Every new observation gets `NF-C<commit>-NNN`.

| ID | Found during | Observation | Classification | Disposition | Status |
|---|---|---|---|---|---|
| NF-C1-001 | Commit 1 | Historical audit test count 28/492 differs from current `121bc58` Actions evidence 27/510/3 subtests | Evidence staleness | Commit 6 final evidence | EVIDENCE UPDATE |
| NF-C1-002 | Commit 1 | `audit/**` is not included in CI push trigger; PR trigger remains configured | CI/release process | Commit 6 | DEFERRED |
| NF-C2-001 | Commit 2 | Generic `swing_utils.atomic_csv()` uses deterministic `<destination>.tmp` | Potential generic artifact race | Post-stabilization | DEFERRED |
| NF-C2-002 | Commit 2 | Generic JSON writers outside V2 are not uniformly atomic | Broader artifact/observability hardening | Post-stabilization unless directly needed by Commit 4 | DEFERRED |
| NF-C3-001 | Commit 3 | DB and backtest used D7/reference-price shortcuts instead of ordered actual-entry lifecycle | Direct child of AF-P1-002 | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-002 | Commit 3 | Profile shadow inferred TP1/TP2/SL rates from final-outcome text, incompatible with canonical final-outcome vocabulary | Lifecycle metric defect | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-003 | Commit 3 | Live Exit Engine has runtime-only exits (broker distribution, decision downgrade, close-below-EMA20) that a price-only historical evaluator cannot reproduce without historical context | Evaluation reproducibility gap outside minimum audited lifecycle contract | Consolidated post-stabilization discussion | DEFERRED |
| NF-C3-004 | Commit 3 | Exit active state did not persist explicit TP1-hit/trailing-active milestone fields | Lifecycle state persistence | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-005 | Commit 3 | Outcome tracker could constrain same-sync post-entry evaluation to the waiting-trigger expiry window instead of continuing through available holding data | Lifecycle evaluation-window defect | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-006 | Commit 3 | Existing regression `test_waiting_trigger_opens_then_closes_at_tp1_with_events` encodes TP1-as-full-close, the exact audited defect | Test-contract staleness | Rewrite against canonical semantics before final release evidence | STALE TEST CONTRACT |
| NF-C3-007 | Commit 3 verification | Exit Engine incremented `Holding_Days` on every invocation, so repeated same-session runs could advance max-hold without a new trading session | Lifecycle session-count defect | Commit 3; same-session idempotency guard + regression | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-008 | Commit 3 verification | Initial backtest facade fallback referenced the patched `evaluate_signal`, creating a recursive compatibility path for legacy no-plan datasets | Implementation verification defect caught before finalization | Commit 3; preserve original baseline callable before patch + regression | IMPLEMENTED / PENDING RE-AUDIT |

Detailed deferred observations are maintained in
`docs/SDE_STABILIZATION_DEFERRED_FINDINGS.md`.

## Guardrail traceability

| Guardrail | Current state |
|---|---|
| `auto_entry_enabled=false` | FROZEN by Commit 1 |
| `MODERATE_BASELINE` | FROZEN by Commit 1 |
| Scoring/weights/thresholds | FROZEN by Commit 1 |
| Hard blockers | FROZEN by Commit 1 |
| Entry-zone calculation | FROZEN by Commit 1 |
| Initial SL calculation | FROZEN by Commit 1 |
| TP1/TP2 price calculation | FROZEN by Commit 1 |
| Closed-candle policy | FROZEN by Commit 1 |
| Broker date/coverage validation | MUST REMAIN |
| Data-quality propagation | MUST REMAIN |
| Exchange/suspended/UMA handling | MUST REMAIN |
| Protected decision columns | MUST REMAIN |
| Deterministic AI fallback | MUST REMAIN |
| Config hash/run manifest | MUST REMAIN |
| SQLite integrity/WAL/FK checks | MUST REMAIN |
| Lifecycle event idempotency | MUST REMAIN |
| Shadow-only profile comparison | MUST REMAIN |

## Release-gate tracking

Commit 6/re-audit must prove:

- no unwaived required-test failures;
- quant freeze passes;
- final run lineage reconstructs V2 -> V3 -> exit -> DB -> delivery;
- V2 publication remains serialized/atomic/hash-consistent;
- engine and resend/delivery statuses are distinct;
- canonical lifecycle semantics are identical across required evaluators;
- canonical data path is the actual production boundary;
- conflicting dates fail closed;
- auto-entry remains false;
- no release-blocking CI/security governance defect lacks explicit waiver.

## Final re-audit closure

Do not mark an `AF-*` finding `CLOSED BY RE-AUDIT` until Commit 6.

For every item record final status, final owning SHA, tests/results, runtime run
ID/evidence, hashes/lineage where relevant, residual risk, and release impact.

The historical AMBER/RED and 64/100 verdict remain provisional until that
re-audit is complete.
