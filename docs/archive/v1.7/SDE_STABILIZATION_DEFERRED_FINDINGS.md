# SDE Stabilization — Deferred Findings Log

> Archived historical backlog evidence. Retained for audit traceability; it is
> not current operational guidance. See `docs/README.md` for active documentation.

## Policy

This file records observations discovered during stabilization that are outside
the agreed P0/P1 scope or require a separate design/governance decision.
Resolved items are retained here when they were previously deferred, so their
history is not lost.

Cumulative status is tracked in:

`docs/archive/v1.7/SDE_STABILIZATION_AUDIT_TRACEABILITY.md`

Stabilization finding IDs use `NF-C<commit>-NNN`; Phase 2 Process 2 findings
use `P2P2-NF-NNN`.

## Commit 1 observations

### NF-C1-001 — Historical CI count is stale relative to current baseline evidence

Status: EVIDENCE UPDATE

Audit history contains `28 failed / 492 passed`. Actions re-characterization on
the audited `121bc58` baseline was `27 failed / 510 passed / 3 subtests passed`,
with compile PASS. Commit 6 final candidate evidence is `565 passed / 3 subtests
passed / 0 failed`.

This updates evidence; it does not rewrite the historical audit observation.

### NF-C1-002 — `audit/**` branch was not covered by CI push trigger

Status: **RESOLVED IN COMMIT 6**

Commit 6 adds direct push coverage for `audit/**` in `.github/workflows/ci.yml`.
The release candidate successfully ran the full audit-branch workflow.

## Commit 2 observations

### NF-C2-001 / DF-C2-001 — Generic `atomic_csv` temp path is deterministic

Status: **CLOSED BY PHASE 2 RE-AUDIT**

`swing_utils.atomic_csv()` uses `<destination>.tmp`. Concurrent generic callers
for the same destination could collide.

Phase 2 Process 2 replaces deterministic temp names with unique same-directory
temps and publishes only after flush/fsync. Concurrency and interruption
regressions pass. The dedicated P0 V2 publisher remains protected.

### NF-C2-002 / DF-C2-002 — Generic JSON writers are broader than V2

Status: **CLOSED BY PHASE 2 RE-AUDIT**

JSON writers outside the audited V2/runtime-status slices are not uniformly
atomic. A global rewrite would span snapshots, manifests and unrelated runtime
state. Phase 2 Process 2 routes active runtime JSON document writers through the
durable shared publisher while retaining the stronger dedicated runtime-status
and V2 guards.

## Commit 3 observations

### NF-C3-003 / DF-C3-001 — Runtime-only exit signals cannot be replayed from price alone

Status: **CLOSED BY PHASE 2 RE-AUDIT**

Live Exit Engine can also exit on time-aligned broker/decision/runtime context,
including strong broker distribution, decision downgrade and EMA-based runtime
signals. The canonical historical lifecycle evaluator reproduces the audited
price-derived TP1/TP2/stop/trailing/max-hold contract but cannot truthfully
invent missing historical broker/decision context.

Post-stabilization work should either supply complete time-aligned historical
context or formally classify those exits as runtime-only overlays with a
separate reproducibility contract.

Phase 2 Commit 3 chose the second truthful boundary. `SDE_SWING_REPLAY_V1`
separates price-reproducible lifecycle, price-derived context, and runtime
context overlay; historical reports explicitly emit `full_live_replay=false`
when context is unavailable or unapplied.

### NF-C3-006 / DF-C3-002 — Existing lifecycle regression encoded TP1 full close

Status: **RESOLVED IN COMMIT 6**

The stale regression was updated to the audited lifecycle contract: TP1 is a
milestone/open state with trailing activation; TP2 is the target close.
Production semantics were not reverted to satisfy the old assertion.

## Commit 4 observations

### NF-C4-006 / DF-C4-001 — Some legacy exception branches use repository-default traceback path

Status: **CLOSED BY PHASE 2 RE-AUDIT**

Some legacy explicit exception branches still resolve traceback output through
the repository-default path rather than `ctx.status_root`. Commit 4 closed the
audited interrupt/status/lock lifecycle without broad path normalization.

Phase 2 Process 2 introduces the context-owned `write_traceback()` helper and
normalizes all active runner exception branches. Default paths remain backward
compatible; custom status roots are covered by regression tests.

## Commit 5 observations

### NF-C5-003 / DF-C5-001 — HISTORICAL_PROVIDER wording contradicts ownership map

Status: **CLOSED BY PHASE 2 RE-AUDIT**

`config/data_sources.json` declares `HISTORICAL_PROVIDER` as the primary owner
for `DailyBar`, while a provider note still says “Fallback only.”

Commit 5 follows the executable ownership map. Documentation/config wording
should be reconciled separately without changing provider priority casually.

Phase 2 Commit 3 reconciles the wording and keeps the executable DailyBar
resolution chain exactly `HISTORICAL_PROVIDER`. The legacy adapter is now
described as the compatibility/acquisition boundary, not as ownership priority.

### NF-C5-004 / DF-C5-002 — Generic canonical quality engine lacks configured BEI holiday injection

Status: **CLOSED BY PHASE 2 RE-AUDIT**

`DataSourceManager` constructs its generic `DataQualityEngine()` without
injecting the repository BEI holiday/special-session configuration.

Phase 2 Process 2 injects the existing calendar through both runtime contexts
and `DataSourceManager`. `NON_TRADING_DAY` is restricted to explicitly
market-session-bound canonical types, so metadata and corporate-action records
are not rejected merely because they arrive on a closed day.

## Phase 2 Process 2 observation

### P2P2-NF-001 - Windows concurrent atomic replace sharing conflict

Status: **CLOSED BY PHASE 2 RE-AUDIT**

The new concurrency regression reproduced transient `WinError 5` while
multiple writers replaced one destination. Because this blocked the requested
generic durability guarantee and remained inside Process 2 scope, the shared
publisher now retries only transient Windows sharing conflicts with a bounded
delay. Interruption still propagates and cleans the unique temp file.

## Commit 6 observations

Commit 6 discovered and resolved five release-cleanup observations:

- `NF-C6-001` — compatibility facade recursion/delegation risk;
- `NF-C6-002` — stale lifecycle/canonical/presentation regression contracts;
- `NF-C6-003` — two distinct Post Market presentation contracts needed explicit separation;
- `NF-C6-004` — generic REPORT ownership ambiguity between router and scheduler;
- `NF-C6-005` — old Yahoo same-session regression contradicted the existing post-close revalidation safety contract.

Status for all five: **RESOLVED IN COMMIT 6**.

No additional Commit 6 observation was deferred merely to make the release gate
green. The remaining deferred list is the explicit set below.

## Historical post-stabilization discussion set

The following was the discussion set before Phase 2 final re-audit. It is
retained as historical scope evidence; all listed implementation findings were
subsequently closed by Phase 2 re-audit.

1. `AF-P2-001` — Telegram idempotency check/write transaction locking.
   **CLOSED BY PHASE 2 RE-AUDIT**.
2. `AF-P2-002` — hotfix workflow write permission / auto-push governance.
   **CLOSED BY PHASE 2 RE-AUDIT**.
3. `AF-P2-003` — full DB revision immutability.
   **CLOSED BY PHASE 2 RE-AUDIT**.
4. `NF-C2-001` — generic `atomic_csv()` deterministic temp path.
   **CLOSED BY PHASE 2 RE-AUDIT**.
5. `NF-C2-002` — generic non-uniform JSON atomicity outside audited slices.
   **CLOSED BY PHASE 2 RE-AUDIT**.
6. `NF-C3-003` — reproducibility contract for runtime-only contextual exits.
   **CLOSED BY PHASE 2 RE-AUDIT**.
7. `NF-C4-006` — legacy traceback path normalization.
   **CLOSED BY PHASE 2 RE-AUDIT**.
8. `NF-C5-003` — HISTORICAL_PROVIDER wording vs ownership map.
   **CLOSED BY PHASE 2 RE-AUDIT**.
9. `NF-C5-004` — manager-wide BEI holiday injection for generic canonical quality validation.
   **CLOSED BY PHASE 2 RE-AUDIT**.
10. `P2P2-NF-001` — Windows concurrent atomic replace sharing conflict.
    **CLOSED BY PHASE 2 RE-AUDIT**.

## Discussion state

Implementation did not equal closure. Phase 2 Commit 3 independently re-audited
the executable behavior and closed the listed findings. The remaining risks are
recorded in `docs/archive/v1.7/SDE_PHASE2_COMMIT3_FINAL_REAUDIT.md`; no historical evidence
above was deleted.
