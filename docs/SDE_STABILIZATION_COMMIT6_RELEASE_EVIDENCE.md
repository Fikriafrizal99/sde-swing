# SDE Stabilization — Commit 6 Release Evidence & Final Re-Audit

## Scope

Commit 6 closes the agreed P0/P1 stabilization sequence. It is release cleanup,
contract validation, CI repair and re-audit evidence. It does not intentionally
change scoring weights, thresholds, hard blockers, Entry/SL/TP price math,
production profile, or auto-entry policy.

Audited baseline:

`121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`

Direct parent of final Commit 6:

`e1344f43a9248fc72b3673edf7dfa91463df6051`

The final Commit 6 SHA is not self-embedded because changing this document would
change that SHA. Git history is authoritative.

## Final candidate re-audit evidence before squash

Workflow: `SDE Swing CI`

- candidate head: `0db823ef0a139791e4eaa91b1fda9b3e3ae0a7f7`
- run ID: `31666990413`
- job ID: `94343606692`
- runner: Ubuntu 24.04
- Python: `3.12.13`
- conclusion: **SUCCESS**

Gate results:

| Gate | Result |
|---|---|
| `python -m compileall -q .` | PASS |
| Quant freeze | PASS |
| Full `pytest -q` | **565 passed, 3 subtests passed, 0 failed** |
| `git diff --check` | PASS |
| Runtime config invariants | PASS (`VALID_WITH_WARNINGS`) |
| Canonical + multi-day contract | PASS |
| Stabilization release gate | PASS |
| Source integrity | PASS |
| Credential scan | PASS |

The dedicated stabilization release validator printed:

- audited baseline recognized: `121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`;
- contracts valid: quant + lifecycle + runtime status + canonical data;
- production profile: `MODERATE_BASELINE`;
- calibration: `SHADOW_ONLY`;
- `auto_entry_enabled=false`.

The canonical/multi-day validator additionally proved:

- 9 canonical record types registered;
- multi-day broker context is context-only and emits no BUY/WATCH/AVOID;
- protected decision columns are not modified by the context bridge;
- credential-less ZAPI behavior remains explicit;
- 12 runtime jobs expose manager metadata and candidate provenance;
- repository entry-point/runtime-artifact structural contract passes.

## Release validator invariants

`tools/ci_validate_stabilization_release.py` makes the release gate executable.
It verifies:

1. the existing quant-freeze validator succeeds against the audited baseline;
2. `MODERATE_BASELINE`, `SHADOW_ONLY`, and `auto_entry_enabled=false` remain
   unchanged;
3. production technical execution remains routed through
   `post_market_validated_runner.py`;
4. broker fusion remains routed through the serialized V2 publisher;
5. closed-candle policy remains `LAST_CLOSED_CANDLE` with partial daily candles
   disabled;
6. lifecycle contract remains `SDE_SWING_LIFECYCLE_V1`;
7. runtime status contract remains `SDE_RUNTIME_STATUS_V1`;
8. canonical historical contract remains `SDE_CANONICAL_DAILY_HISTORY_V1`;
9. conflicting candidate market dates dynamically return no winner and fail
   closed;
10. required stabilization artifacts and audit-branch CI coverage remain present.

## CI cleanup performed

The red baseline was not made green by weakening quant rules. Cleanup addressed
stale or contradictory contracts and stabilization compatibility regressions:

- TP1-as-full-close tests were corrected to TP1 milestone/open + trailing, with
  TP2 as target close;
- Exit/outcome facade delegates were made non-recursive while retaining frozen
  baseline quant implementation;
- legacy OPEN and canonical-input fixtures were aligned to the approved
  lifecycle/data contracts;
- Final Watchlist compact presentation was reconciled with required broker/setup
  sections;
- Post Market diagnostic `CURRENT_V2` and market-first default layouts were
  explicitly separated;
- generic Telegram Report ownership remains isolated from named report routes,
  with scheduler/delivery fallback and definitive topic validation;
- Active Portfolio remains attention-only in Telegram while deterministic HOLD
  reasoning remains available in report data;
- the same-session Yahoo regression now reflects intentional post-close
  revalidation and proves fixture mode remains offline.

## Original P0/P1 closure

| Finding | Final status | Re-audit basis |
|---|---|---|
| AF-P0-001 — CI suite red | **CLOSED BY RE-AUDIT** | 565 passed + 3 subtests; complete release workflow green. |
| AF-P0-002 — V2 shared writer race | **CLOSED BY RE-AUDIT** | Commit 2 serialized/atomic publisher + full artifact-integrity regressions green. |
| AF-P1-001 — canonical layer not production boundary | **CLOSED BY RE-AUDIT** | Commit 5 canonical wrapper/data-path regressions + release validator green. |
| AF-P1-002 — lifecycle semantics diverge | **CLOSED BY RE-AUDIT** | `SDE_SWING_LIFECYCLE_V1` cross-path regressions green. |
| AF-P1-003 — resend overwrites engine status | **CLOSED BY RE-AUDIT** | Engine/delivery separation regressions green. |
| AF-P1-004 — interrupt terminalization inconsistent | **CLOSED BY RE-AUDIT** | Interrupt/exit-code/traceback/lock regressions green. |
| AF-P1-005 — date conflict did not fail closed | **CLOSED BY RE-AUDIT** | Resolver regression + dynamic release-gate assertion green. |

## Deferred scope preserved

Commit 6 does not close or silently implement:

- `AF-P2-001` Telegram idempotency transaction locking;
- `AF-P2-002` hotfix workflow governance;
- `AF-P2-003` full DB revision immutability;
- the explicitly deferred NF items listed in
  `docs/SDE_STABILIZATION_DEFERRED_FINDINGS.md`.

CI runs without live provider/Telegram credentials, so the re-audit is proof of
code/config/contracts—not proof of current external endpoint availability.

## Final posture

**P0/P1 stabilization gate: PASS / GREEN.**

This is a supervised/shadow release gate, not authorization for autonomous order
entry. `auto_entry_enabled=false` and `SHADOW_ONLY` remain frozen.

The original `64/100 — AMBER/RED` audit remains an immutable historical baseline.
No replacement numeric score is assigned because the original audit did not
provide a reproducible score-recalculation formula.

After the atomic history squash, the final Commit 6 SHA must run the same GitHub
Actions workflow successfully. That final-SHA Actions run is the authoritative
branch-level confirmation that the six-commit tree matches this evidence.
