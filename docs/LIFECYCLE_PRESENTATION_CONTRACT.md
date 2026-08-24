# Lifecycle Presentation Contract

Status: canonical presentation contract for SDE Swing lifecycle reporting.

## Purpose

This document fixes one presentation contract so Active Recommendations and Lifecycle Digest do not drift between automatic delivery, manual send, preview, and resend paths.

The single source of truth for **official runtime** lifecycle Telegram presentation is:

`modules/analytics/lifecycle_presentation.py`

No new runtime module or tool may own a second implementation of the Active Recommendations or Lifecycle Digest formatter.

### Frozen baseline exception

`modules/analytics/outcome_tracker_baseline.py` intentionally retains historical formatter code as part of the frozen baseline/compatibility layer. That legacy code is **not** the official output owner. `modules/analytics/outcome_tracker.py` installs the canonical presentation functions over the baseline hooks used by official sync/runtime execution, and direct baseline CLI execution is already blocked. Future presentation changes must therefore modify the canonical presentation module, not the frozen baseline formatter bodies.

## Scope Boundary

This contract is presentation and delivery only. It must not change:

- Decision Engine scoring, thresholds, vetoes, or decisions.
- Technical calculations.
- Broker or foreign-flow calculations.
- Entry, stop loss, TP1, TP2, risk-reward, trigger, or expiry formulas.
- Lifecycle state transitions or SQLite ownership rules.
- Recommendation history semantics.
- `sync_outcome_tracker` state/calculation behavior.

Lifecycle state remains owned by `modules/analytics/outcome_tracker.py`, `outcome_tracker_baseline.py`, and `lifecycle_contract.py`.

## Canonical Builders

Only these canonical runtime builders own Telegram text:

- `build_active_message(active)` — Active Recommendations.
- `build_lifecycle_message(events, max_events=20)` — material Lifecycle Digest.
- `actionable_snapshot(active)` — canonical presentation validation/filter for OPEN and WAITING_TRIGGER rows.

Consumers must import and reuse these functions. Copying their formatting logic into a tool, scheduler, bridge, or menu is prohibited.

## Active Recommendations Contract

The card contains only actionable lifecycle rows:

- `OPEN` -> section `ACTIVE`.
- `WAITING_TRIGGER` -> section `WAITING ENTRY`.
- terminal states such as CLOSED, EXPIRED, and INVALIDATED_BEFORE_ENTRY are excluded.

The table keeps the canonical compact fields:

- EMT
- ENTRY
- NOW
- P/L or GAP
- SL
- TP1
- TP2
- REC
- AGE

`AGE` means lifecycle age in IDX market sessions. The underlying analytics calculation remains `market_session_age` / backward-compatible `age_sessions` from the outcome tracker.

### Last Scan

`scan_staleness_sessions` and related freshness state remain stored and calculated for diagnostics and internal operations.

`Last Scan` is intentionally **not rendered in Telegram**. It is not lifecycle AGE and must not be reintroduced into the Active Recommendations card unless this contract is explicitly revised.

## Lifecycle Digest Contract

Only material lifecycle events are rendered. Current material types are owned by the outcome tracker and include:

- ENTRY_TRIGGERED
- TP1_HIT
- TP2_HIT
- STOP_LOSS_HIT
- MAX_HOLD_EXIT
- EXPIRED
- INVALIDATED_BEFORE_ENTRY

Noise/audit events such as SIGNAL_RECONFIRMED and generic CLOSED rows are not rendered as standalone material lifecycle notifications.

TP1 remains non-terminal and is presented as trailing active. Expiry presentation uses the outcome tracker's existing IDX-session expiry rule and recommendation history count.

## Runtime Flow

### Final Watchlist

Canonical Final Watchlist flow:

1. `tools/run_final_watchlist_entrypoint.py`
2. official Final Watchlist child runs normally
3. Final Watchlist calls `sync_outcome_tracker`
4. outcome tracker writes canonical analytics artifacts
5. material lifecycle status changes are available to the existing Final Watchlist delivery lane
6. after a successful **live** Final Watchlist completion, `tools/send_active_recommendations.py` sends the canonical Active Recommendations card
7. isolated Watchlist AI runs afterward as a separate non-blocking lane

Failure of the Active Recommendations presentation step does not replace the official Final Watchlist result.

`--no-telegram` suppresses the downstream Active Recommendations send. `--dry-run` also skips that downstream send because dry-run intentionally does not persist a fresh lifecycle sync; an older Active Recommendations CSV must never be presented as if it came from the current dry-run.

The canonical entrypoint is shared by the normal Final Watchlist launcher, scheduled Final Watchlist wrapper, and full-daily broker-period orchestration, so these paths do not own separate Active Recommendations formatting logic.

## Canonical Artifacts

Outcome Tracker sync owns these presentation artifacts:

- `data/output/analytics/performance/ACTIVE_RECOMMENDATIONS.csv`
- `data/output/analytics/performance/ACTIVE_RECOMMENDATIONS_TELEGRAM.txt`
- `data/output/analytics/performance/STATUS_CHANGES_TELEGRAM.txt`
- `data/output/analytics/performance/LIFECYCLE_EVENTS.csv`
- `data/output/analytics/performance/SIGNAL_OUTCOME_LEDGER.csv`

`ACTIVE_RECOMMENDATIONS_TELEGRAM.txt` and `STATUS_CHANGES_TELEGRAM.txt` are generated through the canonical builders installed by `modules/analytics/outcome_tracker.py`.

## Consumer Map

The following official routes must reuse the canonical presentation module:

- Outcome Tracker sync -> canonical Active + Lifecycle artifacts.
- `tools/send_active_recommendations.py` -> canonical Active builder.
- `tools/send_lifecycle_digest.py` -> canonical Lifecycle builder.
- `tools/preview_lifecycle_digest.py` -> canonical Lifecycle builder.
- Final Watchlist entrypoint -> canonical Active sender after successful live official Final Watchlist.
- Performance & Evaluation menu -> the same canonical send tools.

Manual preview/resend must never introduce a separate formatter.

## Change Control

Any future lifecycle Telegram formatting change must follow all rules below:

1. Change `modules/analytics/lifecycle_presentation.py` first.
2. Do not duplicate runtime formatter code in tools or job runners.
3. Do not edit frozen baseline formatter bodies merely to change presentation; official facade hooks must continue to point to the canonical builders.
4. Keep engine/state calculations unchanged unless a separate engine change is explicitly approved.
5. Update presentation contract tests in the same change.
6. Preserve the distinction between lifecycle AGE and scan freshness.
7. Keep Active Recommendations and material Lifecycle Digest as separate concepts/cards.
8. Preserve duplicate actionable-symbol fail-closed validation.
9. A dry-run must not reuse stale persisted Active Recommendations as current output.

## Regression Tests

The presentation contract is guarded by:

- `tests/test_evaluation_telegram_cards.py`
- `tests/test_lifecycle_presentation_contract.py`
- lifecycle state tests such as `tests/test_lifecycle_rec_age_expiry.py`

The contract tests verify that user-facing tools reuse the same canonical builders, official baseline hooks point to the canonical facade, `Last Scan` stays hidden while AGE remains present, and Final Watchlist keeps Active Recommendations downstream/non-blocking.

## Non-Goals

This refactor does not redesign lifecycle logic, change recommendation eligibility, change expiry duration, alter TP/SL handling, or change any Decision Engine output.
