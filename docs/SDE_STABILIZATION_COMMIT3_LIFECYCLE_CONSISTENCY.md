# SDE Stabilization — Commit 3 Lifecycle Consistency

## Audit basis

Commit 3 owns `AF-P1-002` from `docs/SDE_AUDIT_BASELINE.md`: lifecycle
semantics were not identical across Exit Engine, outcome tracker, SQLite
outcome archive, profile shadow evaluation, and backtest/evaluation.

The audited contract is:

- TP1 is a milestone that activates trailing; it is **not** a mandatory full close;
- TP2 is the full target close;
- when stop and target are both inside one daily candle, stop has conservative priority;
- max-hold is counted from executable holding sessions;
- evaluation uses actual executed entry where a plan/trigger exists, not a reference close substituted as execution;
- entered-trade outcome is `WIN`, `LOSS`, `AMBIGUOUS`, or `OPEN`;
- entry, initial stop, TP1, and TP2 **price calculations remain frozen**.

This commit does not change `MODERATE_BASELINE`, scoring, thresholds, hard
blockers, candidate selection, entry-zone calculation, initial-stop
calculation, TP1 calculation, TP2 calculation, RR thresholds, or auto-entry.

## Root inconsistencies confirmed

### Outcome tracker

The baseline tracker closed a trade immediately when TP1 was touched and
recorded a realized WIN. This contradicted Exit Engine, which already kept TP1
as an active trailing milestone.

### SQLite outcome archive

`archive_watchlist_outcomes()` used a D7 high/low shortcut and reference-price
fallback. It could label TP1 as a final WIN without reconstructing event order
or actual trigger entry.

### Backtest

`evaluate_signal()` used D7 extrema booleans and treated TP1 as a final WIN.
A stop and target inside the same evaluation window could become AMBIGUOUS even
when the audited daily-candle rule requires stop priority per candle.

### Profile shadow

Target/stop rates were inferred from text inside `Final_Outcome`. Once the
canonical final-outcome vocabulary is `WIN/LOSS/AMBIGUOUS/OPEN`, that inference
cannot reliably tell whether TP1, TP2, or stop was hit.

### Exit active state

Exit Engine already used TP1-as-trailing semantics, but the active state did not
persist explicit TP1/trailing fields. Repeated processing therefore lacked one
canonical milestone state.

Verification also found that `Holding_Days` was incremented per invocation,
which could advance max-hold twice if the same closed market session was
processed more than once. Commit 3 makes holding-session progression idempotent
against the persisted `Last_Update` date.

## Implementation

### 1. Canonical lifecycle contract

New module:

`modules/analytics/lifecycle_contract.py`

Contract version:

`SDE_SWING_LIFECYCLE_V1`

It owns execution semantics only. Plan-price ownership remains outside this
module.

Canonical bar order:

1. read the stop that existed before the candle;
2. evaluate stop against daily low;
3. if stop and target coexist in the candle, stop wins;
4. otherwise TP2 closes the trade;
5. otherwise first TP1 records a milestone and keeps the trade OPEN;
6. calculate +1R breakeven / +1.5R EMA20-0.5ATR trailing from the close;
7. the newly calculated stop becomes effective on the next candle;
8. if still open and max-hold is reached, close at that candle close.

This avoids retroactively applying a stop calculated from the same candle.

### 2. Baseline-preserving facades

To minimize blast radius, the audited modules are retained byte-for-byte under
`*_baseline.py`, while their existing public paths become thin lifecycle
facades.

Baseline copies:

- `modules/exit_engine/exit_engine_baseline.py`
- `modules/analytics/outcome_tracker_baseline.py`
- `modules/database/swing_history_db_baseline.py`
- `modules/backtesting/backtest_engine_baseline.py`
- `modules/analytics/profile_shadow_baseline.py`

Public caller/CLI paths remain unchanged.

### 3. Exit Engine state

The facade preserves the existing trailing formula and dynamic runtime exits,
but adds explicit state:

- `TP1_Hit`
- `TP1_Hit_Date`
- `Trailing_Active`
- `Lifecycle_Contract_Version`

TP1 alone leaves `Status=ACTIVE`. TP2 remains a full exit. Stop/target same
candle remains stop-priority. `Holding_Days` advances only when the latest
closed candle date differs from persisted `Last_Update`, preventing same-session
reruns from consuming extra max-hold sessions.

### 4. Outcome tracker

The tracker now:

- persists `current_stop`, `trailing_active`, `tp1_hit_date`,
  `last_evaluated_date`, and lifecycle contract version;
- keeps TP1 as OPEN and emits a TP1 milestone;
- continues from TP1 toward TP2, trailing stop, or max-hold;
- can emit ordered lifecycle milestones when historical data spans multiple events;
- continues holding evaluation beyond the trigger-expiry window after valid entry;
- shows TP1 Telegram lifecycle text as a trailing milestone rather than an Exit.

Pre-entry `EXPIRED` and `INVALIDATED_BEFORE_ENTRY` states remain for backward
compatibility and are excluded from executed-trade outcome semantics.

### 5. Database outcome archive

`watchlist_outcomes` now derives actual entry from the executable plan trigger
when possible and runs the same lifecycle evaluator.

Legacy D7-named schema columns are retained for compatibility, but TP1/TP2/SL
flags and final outcome come from ordered lifecycle evaluation rather than an
unordered seven-day extrema shortcut.

### 6. Backtest

Plan-backed backtests now:

- carry entry-zone/trigger fields into evaluation;
- derive actual trigger execution;
- use the canonical lifecycle path;
- keep TP1 open;
- apply stop-priority per daily candle;
- respect configured max-hold;
- expose explicit TP1/TP2/SL hits, final outcome and exit reason.

Legacy datasets without executable plans retain a compatibility fallback marked
`LEGACY_NO_EXECUTABLE_PLAN` rather than being presented as canonical evidence.
The original baseline `evaluate_signal()` callable is captured before facade
patching, so this compatibility path cannot recurse into the new facade.

### 7. Profile shadow

Shadow metrics prefer explicit canonical TP1/TP2/SL hit columns. Outcome-text
parsing remains only as compatibility fallback when explicit hit evidence is absent.

## Quant freeze protection

Deliberately unchanged:

- production profile and weights;
- candidate/setup scoring thresholds;
- hard blockers;
- `auto_entry_enabled=false`;
- entry-zone price calculation;
- initial-stop price calculation;
- TP1/TP2 price calculation;
- RR and max-risk configuration.

Existing audited source is retained under baseline modules, making the
lifecycle-only delta explicit.

## Regression coverage

Targeted regression files:

- `tests/test_lifecycle_contract_v1.py`
- `tests/test_lifecycle_commit3_verification.py`

Coverage includes TP1 remaining OPEN, later TP2 close, same-candle stop
priority, max-hold session counting, tracker TP1/TP2 persistence, Exit Engine
TP1 state, same-session holding-day idempotency, shadow explicit hit fields,
backtest actual trigger entry, and non-recursive legacy fallback.

The pre-existing test
`test_waiting_trigger_opens_then_closes_at_tp1_with_events` encodes the audited
defect and is tracked as a stale lifecycle regression until rewritten to the
canonical contract. It must not be used as evidence that old TP1 behavior is correct.

## New findings discovered in Commit 3

Tracked in `SDE_STABILIZATION_AUDIT_TRACEABILITY.md`:

- DB/backtest D7/reference shortcut — resolved in this commit;
- shadow TP hit-rate inference from outcome text — resolved;
- missing explicit TP1/trailing persistence — resolved;
- trigger-expiry window could truncate same-sync post-entry evaluation — resolved;
- repeated same-session Exit Engine runs could increment holding age twice — resolved;
- initial facade verification exposed a recursive legacy backtest fallback —
  fixed before Commit 3 finalization and covered by regression;
- runtime-only broker/decision/EMA exits cannot be fully replayed from a pure
  price evaluator without historical context — deferred;
- one pre-existing regression encodes TP1-as-full-close — stale test contract.

## Acceptance

Commit 3 is `IMPLEMENTED / PENDING RE-AUDIT` when:

- required production lifecycle paths share `SDE_SWING_LIFECYCLE_V1` semantics
  for TP1/TP2/same-candle/max-hold/actual entry;
- same-session reruns do not advance max-hold age twice;
- TP1 never becomes mandatory full close in canonical plan-backed evaluation;
- price levels remain unchanged;
- new findings are recorded;
- targeted lifecycle regression exists;
- quant freeze remains expected to pass.

The finding becomes `CLOSED BY RE-AUDIT` only after Commit 6 evidence.
