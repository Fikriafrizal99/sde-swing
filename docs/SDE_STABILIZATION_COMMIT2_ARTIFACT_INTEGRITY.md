# SDE Stabilization — Commit 2 Artifact Integrity

## Audit basis

Commit 2 closes the P0 artifact-integrity finding recorded in
`docs/SDE_AUDIT_BASELINE.md`:

- shared `FINAL_DECISION_V2.csv` writer was not entirely protected by the
  global writer lock;
- the audit therefore classified concurrent overwrite/race as a potential
  risk;
- the artifact contract requires Fusion V2 lineage to carry technical hash,
  broker hash, run ID, and auditable output hash;
- the evidence hierarchy prefers run-scoped/hash-addressable evidence over a
  shared `*_latest` compatibility path.

This commit does not change Broker Fusion scoring, candidate selection,
decision thresholds, entry calculation, stop-loss calculation, target
calculation, or any other protected quant behavior.

## Root cause

Production jobs invoke Broker Fusion through
`run_broker_fusion_from_snapshot()`, but the global resource-lock coverage is
job-dependent. A standalone `broker_summary` run can invoke the same shared V2
writer while another write-capable flow exists.

The prior Broker Fusion engine also wrote directly to the shared canonical CSV
before publishing its manifests. The CSV had source hashes in its manifest, but
there was no run-scoped immutable V2 copy that could remain authoritative if
the shared compatibility path was later replaced.

## Implementation

### 1. Production route is moved to an integrity publisher

`config/pipeline.json` now routes `paths.broker_fusion` to:

`modules/broker_fusion/broker_fusion_publisher.py`

The existing quant engine remains:

`modules/broker_fusion/broker_fusion.py`

The publisher imports and calls the existing `fuse()` function. Quant
ownership therefore remains in the frozen engine.

### 2. Exclusive V2 writer lock

Before Broker Fusion starts, the publisher acquires:

`data/state/artifacts/FINAL_DECISION_V2.writer.lock`

The lock covers both calculation of the run-scoped artifact and publication to
the shared canonical artifact. A second writer fails closed while the lock is
active.

The lock includes run ID, PID, host, and creation timestamp. Stale cleanup is
bounded and does not remove a same-host lock whose owner is still alive.

### 3. Run-scoped evidence first

The existing Broker Fusion engine writes first to:

`data/output/fusion/<run_id>/FINAL_DECISION_V2.csv`

This is the run-specific evidence artifact. It is never used as a shared
latest path.

The associated manifest is later enriched with:

- `Run_ID`
- `run_scoped_output`
- `run_scoped_output_hash`
- `canonical_output`
- `canonical_output_hash`
- `artifact_integrity_version`
- `publication_status`
- `published_at`
- `writer_lock`
- `lineage_complete`

Existing source lineage fields from Broker Fusion are preserved, including
technical, broker-summary, and broker-raw hashes.

### 4. Atomic canonical publication

Only after the run-scoped V2 artifact is complete does the publisher copy it to
a unique temporary file in the canonical destination directory.

The temporary file is flushed and `fsync()` is called before `os.replace()`
publishes it as:

`data/input/FINAL_DECISION_V2.csv`

Because the replacement occurs within the same destination directory, readers
observe either the previous complete canonical CSV or the newly completed CSV,
not a partially-written intermediate file.

### 5. Hash equality is mandatory

The publisher calculates SHA-256 for both:

- run-scoped V2;
- canonical V2 after publication.

Publication fails if the hashes differ.

For downstream compatibility, the existing manifest field `output` still
points to the canonical `FINAL_DECISION_V2.csv`, while the immutable
run-scoped location is exposed separately.

### 6. Stale canonical sidecar protection

A narrow guard was added to `swing_utils.write_json()` only for:

`FINAL_DECISION_V2.manifest.json`

If a delayed writer attempts to overwrite the canonical sidecar with an
`output_hash` that no longer matches the current canonical CSV, the write is
rejected with:

`STALE_FINAL_DECISION_V2_MANIFEST_WRITE_BLOCKED`

This closes the gap where a previous run could finish a post-fusion sidecar
update after a newer run had already published a different canonical V2.

Other JSON write behavior is intentionally left unchanged in Commit 2.

## Compatibility contract

The following remain unchanged:

- canonical V2 path: `data/input/FINAL_DECISION_V2.csv`;
- Broker Fusion calculation: existing `broker_fusion.py::fuse`;
- output columns and row calculation;
- broker score and confirmation semantics;
- downstream `decision_source`;
- production profile and decision policy;
- `auto_entry_enabled=false`.

The change is therefore an artifact publication/lineage hardening layer, not a
quant change.

## Failure behavior

Commit 2 intentionally fails closed for artifact integrity:

- active V2 writer lock -> writer rejected;
- missing/empty run-scoped V2 -> publication rejected;
- run-scoped/canonical SHA mismatch -> publication rejected;
- invalid run-scoped manifest -> publication rejected;
- stale canonical V2 sidecar hash -> sidecar write rejected.

A failed publication does not intentionally rewrite the canonical V2 with
partial bytes.

## Regression coverage

`tests/test_artifact_integrity_v2.py` covers:

1. production config routes through the integrity publisher;
2. concurrent V2 writer lock rejection;
3. run-scoped/canonical publication and SHA equality;
4. stale canonical V2 sidecar rejection;
5. matching canonical sidecar acceptance.

Isolated Commit 2 smoke validation:

`5 passed`

The full repository suite remains a later stabilization/release gate. Commit 2
does not make unrelated historical tests green by changing quant behavior.

## Commit 2 acceptance

Commit 2 is complete when:

- the production Broker Fusion route passes through the V2 integrity publisher;
- only one V2 writer can execute at a time through the production route;
- every new V2 run has a run-scoped evidence copy;
- canonical publication is atomic;
- canonical and run-scoped SHA-256 values match;
- canonical/run manifests preserve source lineage and run ID;
- stale sidecar overwrite is rejected;
- frozen quant engine/source remains unchanged.

The production release verdict remains unchanged until all stabilization
commits are complete and the system is re-audited.
