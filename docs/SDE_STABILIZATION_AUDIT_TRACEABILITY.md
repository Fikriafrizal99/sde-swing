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
- Approved operation until re-audit: supervised/shadow only

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
- `CLOSED BY RE-AUDIT`
- `DEFERRED`
- `EVIDENCE UPDATE`
- `STALE TEST CONTRACT`

## Master audit finding register

| ID | Priority | Audit finding | Owner | Current status | Evidence / next proof |
|---|---|---|---|---|---|
| AF-P0-001 | P0 | CI suite red | Commit 6 | OPEN | Historical audit 28 failed / 492 passed. Current audited-base Actions evidence: 27 failed / 510 passed / 3 subtests passed; compile PASS. |
| AF-P0-002 | P0 | Shared `FINAL_DECISION_V2.csv` writer not fully serialized/locked | Commit 2 | IMPLEMENTED / PENDING RE-AUDIT | Dedicated V2 writer lock, run-scoped artifact, atomic canonical publish, SHA equivalence, stale sidecar rejection. |
| AF-P1-001 | P1 | Canonical data layer is not production execution boundary | Commit 5 | OPEN | DataSourceManager exists; audited Stage 1/2 still use legacy execution paths. |
| AF-P1-002 | P1 | TP1/lifecycle semantics differ across Exit Engine, tracker, DB, shadow and backtest | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT | `SDE_SWING_LIFECYCLE_V1`; TP1 milestone/open, TP2 close, same-candle stop priority, max-hold, actual trigger entry. |
| AF-P1-003 | P1 | Resend/delivery can overwrite engine `*_latest.json` | Commit 4 | IMPLEMENTED / PENDING RE-AUDIT | `SDE_RUNTIME_STATUS_V1`: engine channel retained; delivery/resend channel added; resend declares `engine_mutation=NONE`. |
| AF-P1-004 | P1 | Interrupt can leave inconsistent terminal state such as FAILED + exit code 0 | Commit 4 | IMPLEMENTED / PENDING RE-AUDIT | Lock-boundary interrupt terminalization, exit 130 evidence, traceback, FAILED/nonzero invariant, ownership-safe release. |
| AF-P1-005 | P1 | Conflicting market dates can select a winner with fail-closed false | Commit 5 | OPEN | Canonical date conflict must block and propagate invalid quality. |
| AF-P2-001 | P2 | Telegram idempotency check/write not transaction-locked | Post-stabilization | DEFERRED | Explicitly outside P0/P1 scope. |
| AF-P2-002 | P2 | Hotfix workflow has `contents: write` and auto-push | Post-stabilization | DEFERRED | Governance/security follow-up. |
| AF-P2-003 | P2 | DB revision history not fully immutable | Post-stabilization | DEFERRED | Full DB revision redesign excluded. |

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

Implementation commits must not rewrite these facts.

## Commit register

### Commit 1 — Audit baseline + quant freeze

Commit: `78231c9f624c287fe0bdc24e090bb5015accecbd`

Status: IMPLEMENTED

Evidence: `config/audit_quant_freeze.json`, `tools/ci_validate_quant_freeze.py`,
`tests/test_audit_quant_freeze.py`, `docs/SDE_STABILIZATION_BASELINE.md`.

### Commit 2 — Artifact integrity

Commit: `07efef3accfc864406a7e7d18a97a10bd3a8ff3b`

Status: IMPLEMENTED / PENDING RE-AUDIT

Evidence: `modules/broker_fusion/broker_fusion_publisher.py`,
`tests/test_artifact_integrity_v2.py`,
`docs/SDE_STABILIZATION_COMMIT2_ARTIFACT_INTEGRITY.md`.

Shared V2 publication remains Commit 2 ownership.

### Commit 3 — Lifecycle consistency

Commit: `3713dae78cbd6b7b4c5dc94cc45d409a9671824f`

Status: IMPLEMENTED / PENDING RE-AUDIT

Contract: `SDE_SWING_LIFECYCLE_V1`

Evidence: `modules/analytics/lifecycle_contract.py`, lifecycle facades +
byte-preserved baseline modules, `tests/test_lifecycle_contract_v1.py`,
`tests/test_lifecycle_commit3_verification.py`, and
`docs/SDE_STABILIZATION_COMMIT3_LIFECYCLE_CONSISTENCY.md`.

### Commit 4 — Runtime / status / locking

Commit: this commit; final SHA is the parent of Commit 5 and will be pinned in
final re-audit.

Status: IMPLEMENTED / PENDING RE-AUDIT

Contract: `SDE_RUNTIME_STATUS_V1`

Evidence:

- `modules/job_runner/runtime.py`
- `modules/job_runner/runtime_baseline.py`
- `modules/runtime/status.py`
- `tests/test_runtime_status_commit4.py`
- `docs/SDE_STABILIZATION_COMMIT4_RUNTIME_STATUS_LOCKING.md`

Semantics:

- `<job>_latest.json` is engine/job owned;
- resend/delivery uses `<job>_delivery_latest.json` plus dated delivery records;
- resend never writes engine latest;
- engine/process status and delivery status are explicit independent fields;
- terminal FAILED cannot retain exit code zero;
- KeyboardInterrupt crossing the job lock writes FAILED/INTERRUPTED, exit 130,
  finished time, errors and traceback before release;
- lock ownership is token/run/PID/host based;
- fresh foreign-host locks are not invalidated by local PID probes;
- stale candidate content is rechecked immediately before unlink.

## New findings discovered during stabilization

| ID | Found during | Observation | Disposition | Status |
|---|---|---|---|---|
| NF-C1-001 | Commit 1 | Historical test evidence 28/492 differs from current audited-base Actions evidence 27/510/3 subtests | Commit 6 evidence | EVIDENCE UPDATE |
| NF-C1-002 | Commit 1 | `audit/**` excluded from CI push trigger; PR trigger remains | Commit 6 | DEFERRED |
| NF-C2-001 | Commit 2 | Generic `swing_utils.atomic_csv()` uses deterministic destination tmp | Post-stabilization | DEFERRED |
| NF-C2-002 | Commit 2 | Generic JSON writers outside V2 are not uniformly atomic | Post-stabilization unless release-blocking | DEFERRED |
| NF-C3-001 | Commit 3 | DB/backtest used D7/reference shortcut rather than ordered actual-entry lifecycle | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-002 | Commit 3 | Shadow inferred TP hit rates from final-outcome text | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-003 | Commit 3 | Runtime broker/decision/EMA exits cannot be perfectly replayed without historical context | Post-stabilization | DEFERRED |
| NF-C3-004 | Commit 3 | Exit state lacked explicit TP1/trailing persistence | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-005 | Commit 3 | Tracker post-entry evaluation could be truncated by trigger-expiry window | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-006 | Commit 3 | Existing regression encodes TP1-as-full-close | Commit 6 test cleanup | STALE TEST CONTRACT |
| NF-C3-007 | Commit 3 verification | Same-session Exit Engine rerun could advance holding age twice | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C3-008 | Commit 3 verification | Initial backtest compatibility facade could recurse | Commit 3 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C4-001 | Commit 4 | Delivery-only resend reused engine status writer and could replace engine latest | Commit 4 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C4-002 | Commit 4 | `KeyboardInterrupt` bypassed `except Exception` and could release job lock without terminal status | Commit 4 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C4-003 | Commit 4 | Status writer accepted terminal FAILED with exit code 0 | Commit 4 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C4-004 | Commit 4 | Lock release unlinked by path without verifying owner identity | Commit 4 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C4-005 | Commit 4 | Stale-lock PID liveness was checked without host ownership | Commit 4 | IMPLEMENTED / PENDING RE-AUDIT |
| NF-C4-006 | Commit 4 | Some legacy explicit exception branches use repository-default traceback path instead of `ctx.status_root` | Post-stabilization path normalization unless Commit 6 proves release-blocking | DEFERRED |

Detailed non-blocking observations remain in
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
| Shared V2 publication | Commit 2 ownership; MUST REMAIN |
| Broker date/coverage validation | MUST REMAIN |
| Data-quality propagation | MUST REMAIN |
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
- resend leaves engine latest byte/content-hash unchanged;
- delivery channel remains independent from engine channel;
- interrupt evidence has terminal status + nonzero code + traceback before lock release;
- lock ownership/stale cleanup regression passes;
- canonical lifecycle semantics remain identical;
- canonical data path is the actual production boundary;
- conflicting dates fail closed;
- auto-entry remains false.

## Final re-audit closure

Do not mark an `AF-*` finding `CLOSED BY RE-AUDIT` until Commit 6.

For each item, record final owning SHA, full tests/results, runtime run ID,
hashes/lineage, residual risk and release impact.

The historical AMBER/RED and 64/100 verdict remain provisional until re-audit.
