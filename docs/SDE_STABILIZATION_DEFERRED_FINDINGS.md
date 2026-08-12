# SDE Stabilization — Deferred Findings Log

## Policy

This file records observations discovered while implementing the stabilization
sequence that are outside the currently-owned commit scope.

Items in this log are not silently fixed in another commit. They are retained
for one consolidated discussion after the audit-driven stabilization sequence
is complete.

The cumulative status of original audit findings and new findings is tracked in
`docs/SDE_STABILIZATION_AUDIT_TRACEABILITY.md`.

New findings use the register ID `NF-C<commit>-NNN`. Deferred detail can also be
referenced here as `DF-C<commit>-NNN`.

## Commit 1 observations

### NF-C1-001 — Historical CI count is stale relative to current baseline evidence

Status: EVIDENCE UPDATE

The audit document contains historical test evidence of `28 failed / 492
passed`. Current GitHub Actions evidence on audited baseline `121bc58` was
re-characterized during Commit 1 as `27 failed / 510 passed / 3 subtests
passed`, while compile remained PASS.

This is not treated as a quant defect and does not alter the audit verdict. The
current result is the working failure inventory; final test evidence remains a
Commit 6 release gate.

### NF-C1-002 — `audit/**` branch is not covered by CI push trigger

Status: DEFERRED

The current `SDE Swing CI` workflow has `pull_request` trigger and push trigger
for `main` and `agent/**`. The stabilization branch
`audit/sde-stabilization` therefore does not receive an automatic CI run merely
from a direct branch push.

This does not justify modifying CI inside Commit 1 or Commit 2. Final validation
must explicitly ensure the stabilization descendant receives complete CI
execution/evidence, owned by Commit 6 unless an earlier PR trigger provides the
required evidence.

## Commit 2 observations

### NF-C2-001 / DF-C2-001 — Generic `atomic_csv` temporary path is deterministic

Status: DEFERRED

The generic helper in `swing_utils.atomic_csv()` uses
`<destination>.tmp`. Concurrent use of that helper for the same destination
could therefore collide before replacement.

Commit 2 does not refactor this generic helper because the P0 V2 path now uses a
dedicated unique-temp publisher and exclusive writer lock.

### NF-C2-002 / DF-C2-002 — Generic JSON writers remain broader than V2

Status: DEFERRED

Several general runtime JSON writers use direct replacement/write behavior
outside the narrow `FINAL_DECISION_V2.manifest.json` guard introduced in
Commit 2.

A global observability/artifact refactor would span status, delivery, snapshot,
and other manifests and is therefore intentionally not folded into the V2 P0
fix. If Commit 4 must touch a specific status writer to close an audited P1
status/interrupt finding, only that directly-owned part may be changed there;
the generic refactor remains deferred.

## Tracking rule for future commits

For Commit 3 onward:

1. every newly observed problem receives an `NF-Cx-NNN` ID;
2. add it to `SDE_STABILIZATION_AUDIT_TRACEABILITY.md` immediately;
3. if outside the current commit scope, add detail to this file and mark it
   `DEFERRED`;
4. do not implement it silently under another finding;
5. after Commit 6/re-audit, discuss all remaining deferred items together.

## Discussion state

Do not treat deferred items as approved implementation work. Review them
together after the audit-driven stabilization sequence is finished.
