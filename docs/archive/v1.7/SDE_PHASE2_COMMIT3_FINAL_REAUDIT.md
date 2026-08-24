# SDE Phase 2 — Final Re-Audit

> Archived historical governance evidence. Retained for audit traceability; it
> is not current operational guidance. See `docs/README.md` for active documentation.

## Baseline

- Repository: `Fikriafrizal99/sde-swing`
- Branch: `audit/sde-stabilization`
- Audit date/timezone: 2026-08-13 / Asia/Jakarta
- `PHASE2_COMMIT3_BASELINE`:
  `7e4ebf5458311230ab1777cf2efcac464c28f026`
- Baseline subject: `harden runtime and data integrity`
- Baseline parent / Phase 2 Commit 1:
  `de8bafe7485c4926bbc85b8575e8ac245934bf89`
- Stabilization Commit 6:
  `662c5ef160c7978237abc58e73e897190daf0632`

The final Commit 3 is the single child of the baseline that contains this
document. Its exact SHA is intentionally not embedded in its own content; Git
history is authoritative.

The tracked and staged worktree was clean before implementation. The existing
untracked directory `data/archive/broker_portfolio_backfill/2026-08-12/` was
not read as implementation input, changed, deleted, or staged.

## Phase 2 Changes

### Commit 1

Commit `de8bafe7485c4926bbc85b8575e8ac245934bf89` changed the legacy hotfix
workflow into read-only validation. It removed source/test rewriting, commit
creation, direct push, persisted checkout credentials, and `contents: write`.

### Commit 2

Commit `7e4ebf5458311230ab1777cf2efcac464c28f026` implemented transactional
Telegram reservations, immutable historical price revisions, generic artifact
durability, context-owned traceback paths, and record-aware BEI calendar
injection. It also fixed in-scope Windows sharing finding `P2P2-NF-001`.

### Commit 3

The final commit:

- defines `SDE_SWING_REPLAY_V1` and persists replay scope/fidelity in historical
  backtest and performance artifacts;
- clarifies that `HISTORICAL_PROVIDER` is the primary DailyBar owner and that
  the legacy adapter is an acquisition/compatibility boundary;
- makes protected quant hashing independent of LF/CRLF while retaining real
  working-tree mutation detection;
- makes resend path assertions separator-neutral;
- extends the release validator with replay and DailyBar ownership gates;
- performs the final evidence-based re-audit of every Phase 2 finding.

## Final Findings Matrix

| Finding | Re-audit conclusion | Final status |
|---|---|---|
| `AF-P2-001` | Concurrent callers serialize through durable reservations; duplicate, retry, failure, stale lease, force, and ownership loss are covered. Guarantee remains explicitly at-least-once. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `AF-P2-002` | Workflow has read-only permission, non-persisted credentials, and no mutation/commit/push path. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `AF-P2-003` | Old and new revisions coexist; current projection selects the newest revision; migration and integrity checks pass. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C2-001` | Generic CSV publication uses unique same-directory temp, flush, fsync, replace, and cleanup. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C2-002` | Active generic JSON document publication is durable; stronger V2/runtime-status paths remain intact. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C3-003` | Historical price lifecycle and live contextual overlays are explicitly separated; reports do not claim full live replay. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C4-006` | Active exception branches with runtime context write and report tracebacks under `ctx.status_root`; default behavior remains compatible. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C5-003` | Ownership wording now agrees with the unchanged executable DailyBar resolution chain. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C5-004` | BEI calendar rules apply to market-session-bound records without rejecting non-market records blindly. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `PA2-NF-001` | Canonical Git-clean hash passes for LF and CRLF and fails for a real protected-source mutation. | **CLOSED BY PHASE 2 RE-AUDIT** |
| `PA2-NF-002` | Resend source paths compare as resolved `Path` objects and pass on Windows without changing production behavior. | **CLOSED BY PHASE 2 RE-AUDIT** |

Process 2 finding `P2P2-NF-001` is also **CLOSED BY PHASE 2 RE-AUDIT**.
No `P2C3-NF-*` finding was discovered.

## Replay Contract

Contract version: `SDE_SWING_REPLAY_V1`.

### `PRICE_REPRODUCIBLE_LIFECYCLE`

Applied by canonical historical evaluation:

- stop loss;
- TP1 milestone with trailing activation;
- TP2 full close;
- breakeven after 1R;
- trailing after 1.5R;
- max hold;
- conservative same-candle stop priority.

### `RUNTIME_CONTEXT_OVERLAY`

Not invented by price-only historical evaluation:

- broker distribution;
- decision downgrade;
- other time-aligned contextual runtime exits.

### `PRICE_DERIVED_CONTEXT`

Separately classified for a direct runtime exit such as
`CLOSE_BELOW_EMA20`. It is theoretically replayable only when the required
time-aligned historical indicator context is available and actually applied.
The standard historical evaluator does not currently claim that overlay.

Default historical metadata is:

```json
{
  "replay_scope": "PRICE_REPRODUCIBLE_LIFECYCLE",
  "replay_fidelity": "PRICE_LIFECYCLE_ONLY",
  "runtime_context_available": false,
  "runtime_context_applied": false,
  "price_derived_context_available": false,
  "price_derived_context_applied": false,
  "full_live_replay": false,
  "divergence_classification": "EXPECTED_CONTEXTUAL_DIVERGENCE"
}
```

Regression evidence proves that the price lifecycle is deterministic, a live
broker-distribution overlay can legitimately close while price-only history
remains open, that divergence is classified, and performance/backtest artifacts
do not claim `FULL_LIVE_REPLAY`.

No live exit decision rule, Entry/SL/TP/RR calculation, or lifecycle price
semantics changed.

## Portability Fixes

### Quant freeze

The prior validator hashed raw working-tree bytes. On Windows, Git-managed CRLF
could therefore differ from the expected LF blob even when repository content
was unchanged.

The validator now delegates canonical content hashing to:

```text
git -c core.autocrlf=input hash-object --path=<protected-path> -- <source>
```

It still hashes the working-tree source, so an actual protected mutation fails.
The expected protected blob hash contract was not changed.

### Resend path

`tests/test_resend_daily_reports.py` now compares resolved `Path` values. The
production resend implementation and dated artifact contract are unchanged.

## Validation Evidence

Local environment: Windows, Python `3.13.14`, Git `2.55.0.windows.3`.

| Validation | Result |
|---|---|
| `python -m compileall -q .` | PASS |
| Replay/lifecycle/outcome/exit/provider/resend/quant/governance/Process 2 targeted aggregate | **94 passed** |
| `python tools/ci_validate_quant_freeze.py` | PASS |
| `python tools/ci_validate_stabilization_release.py` | PASS |
| `python tools/ci_validate_runtime_config.py` | PASS (`VALID_WITH_WARNINGS`) |
| `python tools/ci_validate_contracts.py` | PASS; canonical, multi-day, bridge, runtime, and structure |
| `python -m pytest -q` | **595 passed, 3 subtests passed, 0 failed** |
| Repository database, read-only `PRAGMA integrity_check` | `ok` |
| Existing database migration on temporary copy | `398708` current rows migrated to `398708` append-only revision rows; integrity `ok` |

The existing Linux CI contract remains valid: Ubuntu/Python 3.12 runs compile,
quant freeze, full pytest, runtime/canonical/release gates, source-integrity
checks, and credential scanning with read-only repository permission. This
local-only task did not push the branch, open a PR, or claim a remote CI run.

## Quant Invariance

- `config/pipeline.json` is unchanged.
- All protected quant source files are unchanged.
- Expected protected Git blob hashes are unchanged.
- Production profile remains `MODERATE_BASELINE`.
- Calibration remains `SHADOW_ONLY`.
- `auto_entry_enabled=false`.
- Scoring, weights, thresholds, blockers, candidate selection, Entry, SL, TP1,
  TP2, RR, and lifecycle price semantics are unchanged.

Commit 3 is contract/portability/re-audit work, not quant optimization.

## Residual Risks

1. Telegram idempotency is not exactly-once. A crash between remote acceptance
   and durable local completion can cause an ambiguous retry.
2. Existing database files migrate lazily on schema initialization. The
   repository runtime database was preserved; its real schema was tested through
   a temporary copy rather than changed during audit.
3. SQLite triggers and application code do not protect against privileged file
   replacement or direct database destruction.
4. Historical evaluation is intentionally `PRICE_LIFECYCLE_ONLY`; contextual
   live divergence remains expected until complete aligned context is supplied.
5. BEI holidays/special sessions require ongoing configuration maintenance.
6. Bounded Windows replace retry can still fail after exhaustion; failure is
   surfaced and temporary files are cleaned.
7. Credential-less CI proves deterministic code/config contracts, not current
   availability of live providers, AI services, or Telegram.

## Final Release Verdict

**Software / reliability readiness: GO WITH HARDENING** for supervised/shadow
operation. All Phase 2 findings pass executable re-audit, and the full local
suite has zero failures. Residual risks above remain operational hardening
items, not hidden guarantees.

Combined operating posture: **SUPERVISED ONLY**.

## Autonomous Trading Verdict

**NO-GO.** Autonomous order entry remains disabled by
`auto_entry_enabled=false`. Phase 2 did not audit or authorize autonomous
execution, broker order placement, live capital controls, or unattended
recovery behavior.
