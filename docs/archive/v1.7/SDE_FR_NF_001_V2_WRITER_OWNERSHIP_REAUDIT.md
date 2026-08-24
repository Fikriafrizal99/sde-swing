# SDE FR-NF-001 — Canonical V2 Writer Ownership Re-Audit

> Archived historical evidence. Retained for audit traceability; it is not
> current operational guidance. See `docs/README.md` for active documentation.

## Baseline and Scope

- Branch: `audit/sde-stabilization`
- Parent: `50100fba84687594a409855befd6fab5b4d9cea4`
- Finding: `FR-NF-001`
- Original finding reopened: `AF-P0-002`
- Severity before remediation: `BLOCKER`
- Scope: canonical `data/input/FINAL_DECISION_V2.csv` ownership only

Quant, scoring, weights, thresholds, hard blockers, candidate selection,
Entry, SL, TP1, TP2, RR, and lifecycle price semantics are unchanged.
`MODERATE_BASELINE`, `SHADOW_ONLY`, and `auto_entry_enabled=false` remain
frozen.

## Root Cause

Commit 2 established a serialized and atomic canonical publisher, but the
broker multi-day bridge subsequently performed two post-publication direct
writes in `modules/job_runner/core.py`:

1. context attachment rewrote the shared canonical CSV; and
2. unavailable-context cleanup rewrote the same canonical CSV.

Those callers did not hold `FINAL_DECISION_V2.writer.lock`. A context job could
therefore read generation A, wait while Broker Summary published generation B,
then overwrite B with its stale A frame and update sidecar hashes to match the
stale bytes.

The audit also found that the runtime fallback still named the legacy raw
fusion CLI, and that broker-period lineage was appended to canonical manifests
by the orchestrator after official publication.

## Writer Ownership After Remediation

Canonical ownership is now:

`Broker Fusion calculation -> official publisher -> writer lock -> run-scoped artifact -> fsync -> atomic canonical replace -> canonical manifest`

Rules enforced by implementation and regression tests:

- `modules/broker_fusion/broker_fusion_publisher.py` is the only active
  runtime owner that writes canonical V2 and its canonical manifest.
- Runtime fallback routing names the official publisher, not the raw CLI.
- Broker-period lineage is read and included by the publisher before the
  canonical manifest is emitted.
- The broker multi-day bridge reads one hash-consistent publisher generation.
- Context enrichment is written atomically to
  `data/output/fusion/<run_id>/FINAL_DECISION_V2_CONTEXT.csv`.
- Context cleanup, when needed for compatibility with an old populated input,
  is also performed only in that derived artifact.
- Decision Engine and database archival receive the same run-scoped decision
  input; `Output_Files.Fusion` continues to identify the immutable canonical
  publisher output.

The derived manifest records its own hash plus canonical source hash, source
run ID, and publication timestamp. It never replaces or edits the canonical
manifest.

## Executable Writer Audit

| Path | Role | Result |
|---|---|---|
| `config/pipeline.json` | Production route | Official publisher |
| `master_pipeline.py` | Production caller | Uses configured official publisher |
| `modules/job_runner/core.py` | Runtime orchestration | No direct `DataFrame.to_csv()`; no canonical sidecar write; official publisher fallback |
| `modules/broker_fusion/broker_fusion_publisher.py` | Canonical owner | Serialized, run-scoped, atomic, hash-consistent |
| `modules/broker_fusion/broker_fusion.py` | Legacy calculation/raw CLI | Not referenced by active runtime launchers; deferred compatibility surface |
| `tools/validate_release.py` | Test tooling | Raw engine output is isolated under a temporary validation directory |

The legacy raw CLI retains its historical default canonical filename. It is
not a production runtime route and was deliberately not redesigned in this
blocker-only change. Direct manual invocation remains a documented deferred
operational risk.

## Regression Evidence

`tests/test_v2_writer_ownership_regression.py` proves:

- active runtime source has no direct canonical V2 writer bypass;
- revision A context processing cannot overwrite concurrently published
  revision B;
- interruption during derived CSV construction leaves canonical V2 unchanged
  and leaves no partial derived artifact;
- canonical CSV hash equals canonical manifest hash after concurrency;
- stale context cleanup produces a context-free derived input while preserving
  canonical bytes;
- publisher owns broker-period lineage.

Existing V2 publisher tests continue to pass.

## Validation Evidence

| Gate | Result |
|---|---|
| `python -m compileall -q .` | PASS |
| V2 ownership + existing publisher tests | `11 passed` |
| Broker/final-watchlist/runtime targeted integration | `127 passed` |
| `python tools/ci_validate_quant_freeze.py` | PASS |
| `python tools/ci_validate_stabilization_release.py` | PASS |
| `python tools/ci_validate_runtime_config.py` | `VALID_WITH_WARNINGS` |
| `python tools/ci_validate_contracts.py` | PASS |
| `python -m pytest -q` | `601 passed, 3 subtests passed` |

## Targeted Re-Audit

| Closure condition | Result |
|---|---|
| No direct executable canonical V2 writer bypass | PASS |
| Concurrent stale overwrite not reproducible | PASS |
| Single publisher ownership proven | PASS |
| Canonical artifact and manifest consistent | PASS |
| Interruption cannot make canonical partial | PASS |
| Quant and frozen operating contract unchanged | PASS |
| Required tests and gates green | PASS |

Final status: **CLOSED BY RE-AUDIT**.

`AF-P0-002` can be closed again based on executable-path inspection,
concurrency/interruption regressions, and complete validation evidence.

## Residual Risk

- The raw Broker Fusion CLI remains callable manually and is deferred because
  it is outside active production routing. Operational procedures must invoke
  the official publisher for canonical output.
- A derived context artifact may truthfully retain an older coherent source
  generation if a newer publisher run completes after its snapshot. It cannot
  overwrite canonical V2; its manifest records the exact source generation.
- Live provider and Telegram availability are not established by this
  credential-less regression suite.

`FR-NF-002`, `FR-NF-003`, and `FR-NF-004` were not changed.
