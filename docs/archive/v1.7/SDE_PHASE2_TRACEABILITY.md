# SDE Phase 2 Traceability

> Archived historical governance evidence. Retained for audit traceability; it
> is not current operational guidance. See `docs/README.md` for active documentation.

## Purpose

This register maps every Phase 2 finding to its implementation boundary,
executable evidence, regression evidence, final re-audit status, and remaining
risk. It supplements, and does not replace, the historical stabilization audit
evidence.

## Commit Chain

| Stage | Commit / authority | Direct parent |
|---|---|---|
| Stabilization Commit 6 | `662c5ef160c7978237abc58e73e897190daf0632` | Git history |
| Phase 2 Commit 1 | `de8bafe7485c4926bbc85b8575e8ac245934bf89` | Stabilization Commit 6 |
| Phase 2 Commit 2 | `7e4ebf5458311230ab1777cf2efcac464c28f026` | Phase 2 Commit 1 |
| Phase 2 Commit 3 | commit containing this register; SHA is Git-authoritative | `7e4ebf5458311230ab1777cf2efcac464c28f026` |

Phase 2 Commit 3 baseline:

`7e4ebf5458311230ab1777cf2efcac464c28f026`

Branch: `audit/sde-stabilization`.

## Frozen Contract

- production profile: `MODERATE_BASELINE`;
- calibration mode: `SHADOW_ONLY`;
- `auto_entry_enabled=false`;
- scoring, weights, thresholds, blockers, candidate selection, entry, SL, TP1,
  TP2, RR, and lifecycle price semantics are unchanged;
- autonomous execution remains disabled.

## Final Findings Matrix

| Finding | Implementation/evidence boundary | Final verification | Final status |
|---|---|---|---|
| `AF-P2-001` | `modules/job_runner/delivery_idempotency.py`; transactional `BEGIN IMMEDIATE` reservation, lease ownership, retries, failure and force attempt history | concurrency/stale/retry/force regressions; delivery contract remains `AT_LEAST_ONCE_WITH_CRASH_AMBIGUITY` | **CLOSED BY PHASE 2 RE-AUDIT** |
| `AF-P2-002` | `.github/workflows/hotfix-exit-volume.yml`; read-only checkout and validation-only workflow | governance regression proves no rewrite, commit, push, or write permission | **CLOSED BY PHASE 2 RE-AUDIT** |
| `AF-P2-003` | append-only `market_prices_daily_revisions`, no-update/no-delete triggers, current/latest projection | revision A/B migration regression, `PRAGMA integrity_check=ok`; a temporary copy of the repository DB migrated `398708` current rows into `398708` revision rows | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C2-001` | `swing_utils.atomic_csv()` unique same-directory temporary publication | concurrent writers, interruption cleanup, flush/fsync/replace regressions | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C2-002` | shared durable JSON/text publication used by active runtime document writers | interruption and fsync regressions; dedicated V2/runtime-status protections retained | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C3-003` | `SDE_SWING_REPLAY_V1`; price lifecycle, price-derived context, and runtime overlay are separate families | deterministic price replay, live broker-overlay divergence, and report/backtest metadata regressions | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C4-006` | context-owned `write_traceback()` under `ctx.status_root` | standard, lock-boundary, and integrated runner custom-root regressions | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C5-003` | config/docs ownership cleanup only; executable priority unchanged | `DailyBar` resolution chain remains exactly `HISTORICAL_PROVIDER`; ZAPI DailyBar remains disabled in production | **CLOSED BY PHASE 2 RE-AUDIT** |
| `NF-C5-004` | configured BEI holidays/special sessions injected only for market-session-bound canonical records | holiday, weekday, weekend, special session, non-market record, and expected-closed DailyBar regressions | **CLOSED BY PHASE 2 RE-AUDIT** |
| `PA2-NF-001` | quant source hashes use canonical Git clean content via `git -c core.autocrlf=input hash-object --path=...` | unchanged source PASS, LF/CRLF equality PASS, real protected mutation FAIL | **CLOSED BY PHASE 2 RE-AUDIT** |
| `PA2-NF-002` | resend regression compares resolved `Path` objects rather than literal separators | Windows local test PASS; comparison is separator-neutral for Linux | **CLOSED BY PHASE 2 RE-AUDIT** |

## In-Scope Finding Discovered During Process 2

| Finding | Re-audit result | Final status |
|---|---|---|
| `P2P2-NF-001` — transient Windows destination sharing conflict during concurrent replace | bounded retry is restricted to transient Windows sharing conflicts; concurrent/interruption regressions and the full Windows suite pass | **CLOSED BY PHASE 2 RE-AUDIT** |

No `P2C3-NF-*` finding was opened. The final re-audit found no new blocking or
unrelated defect that required a silent fix.

## Replay Evidence

The default historical contract emits:

```text
replay_contract_version=SDE_SWING_REPLAY_V1
replay_scope=PRICE_REPRODUCIBLE_LIFECYCLE
replay_fidelity=PRICE_LIFECYCLE_ONLY
runtime_context_available=false
runtime_context_applied=false
full_live_replay=false
divergence_classification=EXPECTED_CONTEXTUAL_DIVERGENCE
```

Backtest rows, backtest summaries/manifests, watchlist outcomes, and performance
summary artifacts carry the flattened or structured form of this evidence.
No live exit rule and no lifecycle price rule changed.

## Validation Evidence

Environment: Windows, Python `3.13.14`, Git `2.55.0.windows.3`.

| Gate | Result |
|---|---|
| `python -m compileall -q .` | PASS |
| Phase 2 targeted aggregate | **94 passed** |
| Full `python -m pytest -q` | **595 passed, 3 subtests passed, 0 failed** |
| Quant freeze | PASS against audited baseline `121bc58b0f6a62dc3a844ee59575fe48ce86cc7d` |
| Stabilization release validator | PASS, including replay and DailyBar ownership |
| Runtime config invariants | PASS (`VALID_WITH_WARNINGS`) |
| Canonical + multi-day contracts | PASS; 9 record types, context-only multi-day bridge, protected columns unchanged |
| Repository DB read-only integrity | `ok` |
| Existing DB migration on temporary copy | `398708` current rows → `398708` immutable revision rows; integrity `ok` |

The Linux CI workflow remains read-only, uses Python 3.12, covers `audit/**`,
and runs compile, quant freeze, full pytest, configuration/contracts, release
validation, source integrity, and credential scanning. No remote CI run is
claimed because Phase 2 Commit 3 is intentionally not pushed or opened as a PR.

## Residual Risks

- Telegram delivery is at-least-once. A process crash after Telegram accepts a
  message but before local completion is durable can produce an ambiguous retry.
- The repository runtime database is migrated lazily by normal schema
  initialization. The original runtime artifact was deliberately not mutated by
  this audit; its migration path was proven on a temporary copy.
- Append-only revision enforcement is application/SQLite-trigger based and does
  not defend against a privileged actor replacing the database file.
- Historical reports intentionally remain partial live replay until complete,
  time-aligned runtime and derived-indicator context is available and applied.
- Calendar correctness still depends on maintaining the configured BEI holiday
  and special-session data.
- Atomic publication substantially narrows interruption/collision risk, but
  storage hardware/filesystem failure and exhausted Windows sharing retries
  remain possible and observable failures.
- CI does not prove current reachability of live market-data, AI, or Telegram
  endpoints because credentials are intentionally absent.

## Final Posture

- Software/reliability release: **GO WITH HARDENING** for supervised/shadow use.
- Combined operating posture: **SUPERVISED ONLY**.
- Autonomous trading: **NO-GO**; `auto_entry_enabled=false` remains mandatory.
