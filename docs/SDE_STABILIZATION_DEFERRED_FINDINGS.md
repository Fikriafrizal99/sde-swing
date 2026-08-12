# SDE Stabilization — Deferred Findings Log

## Policy

This file records observations discovered during stabilization that are outside
the currently-owned P0/P1 commit scope or cannot be safely expanded without a
separate design decision.

Items here are not silently fixed. They remain visible for one consolidated
discussion after the audit-driven stabilization sequence is complete.

Cumulative status is tracked in:

`docs/SDE_STABILIZATION_AUDIT_TRACEABILITY.md`

New finding IDs use `NF-C<commit>-NNN`.

## Commit 1 observations

### NF-C1-001 — Historical CI count is stale relative to current baseline evidence

Status: EVIDENCE UPDATE

Audit history contains `28 failed / 492 passed`. Current Actions evidence on
audited `121bc58` was re-characterized as `27 failed / 510 passed / 3 subtests
passed`, with compile PASS.

This changes the working failure inventory, not the audit verdict.

### NF-C1-002 — `audit/**` branch is not covered by CI push trigger

Status: DEFERRED

Current CI has pull-request coverage and push coverage for `main` / `agent/**`,
but not direct push coverage for `audit/**`.

Commit 6 must obtain complete final CI evidence; earlier commits do not broaden
scope merely to change workflow triggers.

## Commit 2 observations

### NF-C2-001 / DF-C2-001 — Generic `atomic_csv` temp path is deterministic

Status: DEFERRED

`swing_utils.atomic_csv()` uses `<destination>.tmp`. Concurrent generic callers
for the same destination could collide.

The P0 V2 path is protected by its own unique-temp publisher and exclusive lock,
so a generic helper refactor is outside Commit 2.

### NF-C2-002 / DF-C2-002 — Generic JSON writers are broader than V2

Status: DEFERRED

Runtime JSON writers outside the narrow V2 sidecar guard are not uniformly
atomic.

A global rewrite would span status, delivery, snapshots and manifests. Commit 4
may change only a status writer directly required to close its audited P1
finding; the generic hardening item remains deferred.

## Commit 3 observations

### NF-C3-003 / DF-C3-001 — Runtime-only exit signals cannot be replayed from price alone

Status: DEFERRED

Live Exit Engine can exit on:

- strong broker distribution;
- decision downgrade;
- close below EMA20.

The canonical historical lifecycle evaluator can reproduce price-derived
TP1/TP2/stop/trailing/max-hold behavior. It cannot truthfully reproduce broker
or decision-history exits unless the historical evaluator receives the
corresponding time-aligned decision/broker context.

The minimum lifecycle contract explicitly required by the audit is unified in
Commit 3. Commit 3 does **not** invent historical broker/decision states merely
to make those extra live exits appear replayable.

Post-stabilization discussion should decide whether historical evaluation should
receive full time-aligned context, or these exits should remain explicitly
classified as runtime-only execution overlays with a separate reproducibility
contract.

### NF-C3-006 / DF-C3-002 — Existing lifecycle regression encodes TP1 full close

Status: STALE TEST CONTRACT

`tests/test_outcome_tracker_lifecycle.py::
test_waiting_trigger_opens_then_closes_at_tp1_with_events`

expects TP1 to close the trade and create a companion `CLOSED` event.

That expectation conflicts directly with the audited lifecycle contract and
with Exit Engine semantics. It must be rewritten to TP1 milestone/open behavior;
production code must not be reverted merely to satisfy this stale assertion.

If the existing test is not rewritten inside Commit 3, it remains an explicit
Commit 6 release-cleanup item and must not receive a waiver that legitimizes the
old TP1 semantics.

## Tracking rule for future commits

For Commit 4 onward:

1. every newly observed problem receives `NF-Cx-NNN`;
2. add it to `SDE_STABILIZATION_AUDIT_TRACEABILITY.md`;
3. if outside current ownership, add detail here and mark `DEFERRED`;
4. do not implement it silently under another finding;
5. discuss remaining deferred items together after Commit 6/re-audit.

## Discussion state

Deferred items are observations, not approved implementation work.
