# Lifecycle Presentation Contract

Status: canonical presentation contract for SDE Swing lifecycle reporting.

## Purpose

This document fixes one presentation contract so Active Recommendations and Lifecycle Digest do not drift between automatic delivery, manual send, preview, and resend paths.

The single source of truth for lifecycle Telegram presentation is:

`modules/analytics/lifecycle_presentation.py`

No other module or tool may own a second implementation of the Active Recommendations or Lifecycle Digest formatter.

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

Only these builders own Telegram text:

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

Canonical manual Final Watchlist flow:

1. `tools/run_final_watchlist_entrypoint.py`
2. official Final Watchlist child runs normally
3. Final Watchlist calls `sync_outcome_tracker`
4. outcome tracker writes canonical analytics artifacts
5. material lifecycle status changes are available to the existing Final Watchlist delivery lane
6. after successful Final Watchlist completion, `tools/send_active_recommendations.py` sends the canonical Active Recommendations card
7. isolated Watchlist AI runs afterward as a separate non-blocking lane

Failure of the Active Recommendations presentation step does not replace the official Final Watchlist result.

`--no-telegram` suppresses the downstream Active Recommendations send. Dry-run behavior remains non-live.

## Canonical Artifacts

Outcome Tracker sync owns these presentation artifacts:

- `data/output/analytics/performance/ACTIVE_RECOMMENDATIONS.csv`
- `data/output/analytics/performance/ACTIVE_RECOMMENDATIONS_TELEGRAM.txt`
- `data/output/analytics/performance/STATUS_CHANGES_TELEGRAM.txt`
- `data/output/analytics/performance/LIFECYCLE_EVENTS.csv`
- `data/output/analytics/performance/SIGNAL_OUTCOME_LEDGER.csv`

`ACTIVE_RECOMMENDATIONS_TELEGRAM.txt` and `STATUS_CHANGES_TELEGRAM.txt` are generated through the canonical builders installed by `modules/analytics/outcome_tracker.py`.

## Consumer Map

The following routes must reuse the canonical presentation module:

- Outcome Tracker sync -> canonical Active + Lifecycle artifacts.
- `tools/send_active_recommendations.py` -> canonical Active builder.
- `tools/send_lifecycle_digest.py` -> canonical Lifecycle builder.
- `tools/preview_lifecycle_digest.py` -> canonical Lifecycle builder.
- Final Watchlist entrypoint -> canonical Active sender after successful official Final Watchlist.
- Performance & Evaluation menu -> the same canonical send tools.

Manual preview/resend must never introduce a separate formatter.

## Change Control

Any future lifecycle Telegram formatting change must follow all rules below:

1. Change `modules/analytics/lifecycle_presentation.py` first.
2. Do not duplicate formatter code in tools or job runners.
3. Keep engine/state calculations unchanged unless a separate engine change is explicitly approved.
4. Update presentation contract tests in the same change.
5. Preserve the distinction between lifecycle AGE and scan freshness.
6. Keep Active Recommendations and material Lifecycle Digest as separate concepts/cards.
7. Preserve duplicate actionable-symbol fail-closed validation.

## Regression Tests

The presentation contract is guarded by:

- `tests/test_evaluation_telegram_cards.py`
- `tests/test_lifecycle_presentation_contract.py`
- lifecycle state tests such as `tests/test_lifecycle_rec_age_expiry.py`

The contract tests verify that user-facing tools reuse the same canonical builders and that `Last Scan` stays hidden while AGE remains present.

## Non-Goals

This refactor does not redesign lifecycle logic, change recommendation eligibility, change expiry duration, alter TP/SL handling, or change any Decision Engine output.
