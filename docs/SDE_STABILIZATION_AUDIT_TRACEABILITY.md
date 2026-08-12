# SDE Stabilization — Audit Traceability Register

## Purpose

This document is the cumulative tracking register for the audit-driven work on
`audit/sde-stabilization`.

It does **not** replace `docs/SDE_AUDIT_BASELINE.md`. The audit baseline remains
the immutable statement of what was observed at audited baseline `121bc58`.
This register answers what happened to every finding after implementation work
started: owner commit, current status, implementation evidence, validation
status, and residual/deferred work.

## Source of truth and branch chain

- Audit baseline: `121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`
- Stabilization branch: `audit/sde-stabilization`
- Package/config version: `1.7.0-multisource`
- Production profile: `MODERATE_BASELINE`
- Auto-entry: `false`
- Approved operation remains supervised/shadow only until final re-audit.

Planned stabilization ownership:

1. Commit 1 — audit baseline + quant freeze
2. Commit 2 — artifact integrity
3. Commit 3 — lifecycle consistency
4. Commit 4 — runtime/status/locking
5. Commit 5 — canonical data path
6. Commit 6 — CI/release cleanup + final re-audit evidence

## Status vocabulary

- `OPEN` — audit finding is not implemented yet.
- `IMPLEMENTED / PENDING RE-AUDIT` — scoped code change exists, but the audit
  finding is not considered closed until final evidence/re-audit confirms it.
- `CLOSED BY RE-AUDIT` — only used after final audit evidence confirms closure.
- `DEFERRED` — intentionally outside the P0/P1 stabilization scope.
- `EVIDENCE UPDATE` — observed evidence changed or was corrected without
  changing production behavior.

## Master audit finding register

| ID | Priority | Audit finding | Baseline classification | Owner | Current status | Evidence / notes |
|---|---|---|---|---|---|---|
| AF-P0-001 | P0 | CI suite red | CONFIRMED ISSUE | Commit 6 | OPEN | Historical audit recorded 28 failed / 492 passed. Commit 1 re-characterized current `121bc58` Actions evidence as 27 failed / 510 passed / 3 subtests passed; compile PASS. Full closure is a release gate. |
| AF-P0-002 | P0 | Shared `FINAL_DECISION_V2.csv` writer not entirely protected by global/shared writer control; overwrite race possible | CONFIRMED ISSUE; race POTENTIAL RISK | Commit 2 | IMPLEMENTED / PENDING RE-AUDIT | Production route now uses `broker_fusion_publisher.py`; dedicated exclusive V2 writer lock, run-scoped V2 evidence, atomic canonical publish, SHA equality check, and stale sidecar rejection. Scoped regression: 5 tests PASS. Quant fusion engine unchanged. |
| AF-P1-001 | P1 | Canonical data layer is not yet the production execution boundary | CONFIRMED ISSUE | Commit 5 | OPEN | DataSourceManager/canonical facade exists, but audited Stage 1/2 execution still relies on legacy historical/technical subprocess paths. |
| AF-P1-002 | P1 | TP1/lifecycle semantics differ across engine/tracker/DB/evaluation paths | CONFIRMED ISSUE | Commit 3 | OPEN | Must unify TP1 trailing activation, TP2 full close, same-candle precedence, max-hold, entry reference, and outcome vocabulary without changing frozen entry/SL/TP price calculation. |
| AF-P1-003 | P1 | Resend/delivery path can overwrite engine `*_latest.json` status | CONFIRMED ISSUE | Commit 4 | OPEN | Engine/job status and resend/delivery status ownership must be separated. |
| AF-P1-004 | P1 | Interrupt can leave inconsistent terminal state such as FAILED with exit code 0 | CONFIRMED ISSUE | Commit 4 | OPEN | Must make terminalization, error/traceback evidence, exit code, finished_at, and lock cleanup consistent. |
| AF-P1-005 | P1 | Conflicting market dates can select a winner while fail-closed is false | CONFIRMED ISSUE | Commit 5 | OPEN | Canonical date conflict must block/propagate invalid data quality instead of loosely choosing one date. |
| AF-P2-001 | P2 | Telegram idempotency check/write is not transaction-locked | POTENTIAL RISK | Post-stabilization | DEFERRED | Explicitly outside P0/P1 branch scope. Preserve for consolidated discussion after stabilization. |
| AF-P2-002 | P2 | Hotfix workflow has `contents: write` and auto-push behavior | CONFIRMED ISSUE | Post-stabilization | DEFERRED | Governance/security work remains outside current stabilization commits. |
| AF-P2-003 | P2 | DB revision history is not fully immutable | POTENTIAL RISK | Post-stabilization | DEFERRED | Do not silently expand Commit 3/4 into full DB revision redesign. |

## Baseline operational facts retained from audit

The validated audit run remains baseline evidence, not a new release verdict:

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

The stabilization branch must not use later implementation work to rewrite this
historical baseline evidence.

## Implemented commit register

### Commit 1 — audit baseline + quant freeze

Commit: `78231c9f624c287fe0bdc24e090bb5015accecbd`

Status: IMPLEMENTED

Purpose:

- root stabilization work at audited `121bc58`;
- freeze `MODERATE_BASELINE` and protected quant behavior;
- record current CI characterization;
- add quant drift validator and regression guard;
- prohibit silent changes to scoring, thresholds, hard blockers, entry, SL, TP,
  risk settings, and auto-entry.

Evidence:

- `config/audit_quant_freeze.json`
- `tools/ci_validate_quant_freeze.py`
- `tests/test_audit_quant_freeze.py`
- `docs/SDE_STABILIZATION_BASELINE.md`

### Commit 2 — artifact integrity

Commit at time of this register: implementation originally created as
`9e68e0dafc9fc324ec50107e776d4ab566f1b079`; if this documentation is amended
into Commit 2, the final Commit 2 SHA is the branch parent of Commit 3 and must
be recorded here/at final re-audit.

Status: IMPLEMENTED / PENDING RE-AUDIT

Purpose:

- serialize V2 publication;
- create run-scoped evidence;
- publish canonical V2 atomically;
- verify run-scoped/canonical SHA equivalence;
- preserve run/source lineage;
- reject stale V2 sidecar publication.

Evidence:

- `modules/broker_fusion/broker_fusion_publisher.py`
- `tests/test_artifact_integrity_v2.py`
- `docs/SDE_STABILIZATION_COMMIT2_ARTIFACT_INTEGRITY.md`
- production fusion engine `modules/broker_fusion/broker_fusion.py` remains the
  frozen calculation owner.

## New findings discovered during stabilization

New observations are never silently converted into implementation scope. They
are assigned an ID and tracked here; detail is retained in
`docs/SDE_STABILIZATION_DEFERRED_FINDINGS.md` when deferred.

| ID | Found during | Observation | Classification | Owner / disposition | Status |
|---|---|---|---|---|---|
| NF-C1-001 | Commit 1 | Historical audit test count `28 failed / 492 passed` is stale versus current Actions evidence on `121bc58`: `27 failed / 510 passed / 3 subtests passed`. | Evidence staleness, not quant defect | Commit 6 release evidence | EVIDENCE UPDATE |
| NF-C1-002 | Commit 1 | CI push trigger covers `main` and `agent/**`, not `audit/**`; the stabilization branch therefore does not receive an automatic push-triggered Actions run, although PR trigger remains configured. | CI/release-process observation | Commit 6 / final validation | DEFERRED |
| NF-C2-001 | Commit 2 | Generic `swing_utils.atomic_csv()` uses deterministic `<destination>.tmp`; concurrent callers for the same destination could collide. | Potential generic artifact race outside dedicated V2 path | Post-stabilization discussion | DEFERRED |
| NF-C2-002 | Commit 2 | Generic JSON writers outside the narrow V2 sidecar path are not uniformly atomic and span status/delivery/snapshot/manifest concerns. | Broader observability/artifact hardening | Post-stabilization discussion unless directly owned by Commit 4 | DEFERRED |

Future new findings must be added to this table as `NF-C<commit>-NNN` and, when
deferred, described in the deferred findings log.

## Guardrail traceability

The following audited guardrails remain mandatory through every commit:

| Guardrail | Current state |
|---|---|
| `auto_entry_enabled=false` | FROZEN by Commit 1 |
| Closed-candle policy | FROZEN by Commit 1 |
| Broker date/coverage validation | MUST REMAIN |
| Data-quality propagation | MUST REMAIN |
| Exchange status / suspended / UMA handling | MUST REMAIN |
| Liquidity and broker-distribution hard blockers | FROZEN by Commit 1 |
| Protected decision columns | MUST REMAIN |
| Deterministic AI fallback | MUST REMAIN |
| Telegram escaping/splitting | MUST REMAIN; P2 enhancements deferred |
| Config hash/run manifest | MUST REMAIN |
| SQLite integrity/WAL/foreign-key checks | MUST REMAIN |
| Lifecycle event idempotency | MUST REMAIN |
| Shadow-only profile comparison | MUST REMAIN |

A guardrail change is not authorized by this stabilization register unless it
is explicitly required to close an audited P0/P1 contract inconsistency and is
covered by regression evidence.

## Release-gate tracking

The branch must not be declared production-grade merely because Commit 2-5 are
implemented. Final sign-off in Commit 6/re-audit must prove at minimum:

- required test suite has no unwaived failures;
- quant freeze still passes;
- final run lineage can be reconstructed from run ID through V2/V3/exit/DB and
  delivery evidence;
- V2 shared output publication is serialized/atomic and hash-consistent;
- engine status and resend/delivery status are distinguishable;
- lifecycle/outcome semantics are identical across evaluators;
- canonical data path is the actual production execution boundary and date
  conflicts fail closed;
- auto-entry remains false;
- security/CI governance has no release-blocking violation or has an explicit
  written waiver.

## Final re-audit closure section

Do not fill this section until Commit 6.

For each `AF-*` item, final audit must record:

- final status: `CLOSED BY RE-AUDIT`, `OPEN`, or `DEFERRED`;
- final commit SHA;
- tests executed and result;
- runtime run ID/evidence paths;
- hashes/lineage evidence where relevant;
- residual risk;
- release impact.

The overall AMBER/RED baseline verdict and 64/100 health score remain historical
and provisional until a full re-audit on the stabilization descendant produces
a new signed-off verdict.
