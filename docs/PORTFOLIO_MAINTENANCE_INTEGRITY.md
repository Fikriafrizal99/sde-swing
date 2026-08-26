# Portfolio Maintenance Integrity Contract

Date introduced: 2026-08-26  
Scope: actual portfolio maintenance only  
Baseline before this patch: `testing@96c6b05`

## Purpose

This patch closes portfolio-maintenance integrity gaps found while reviewing real historical positions such as TPIA and MDKA. The goal is to make actual portfolio data reproducible as-of the user's transaction date without changing the trading engine's scoring, broker formulas, discovery logic, decision thresholds, exit rules, or report semantics.

## Invariants

### 1. Pre-BUY setup and executable plan stay coherent

While a signal lifecycle is still `WAITING_TRIGGER`, a later `BUY CONFIRMED` recommendation may upgrade the executable plan. When that happens, `setup_type` must move together with the confirmed trigger/entry/SL/TP plan already updated by the baseline tracker.

Example:

- Day 1: `PULLBACK`, WAITING
- Day 2: `BREAKOUT`, BUY CONFIRMED
- Before actual portfolio BUY: active lifecycle uses `BREAKOUT` together with Day-2 entry/SL/TP.
- After actual portfolio BUY: `position_initial_plan` remains the frozen transaction thesis and later scans do not silently rewrite it.

This patch does **not** change the existing lifecycle transition rules or the baseline condition that controls when a waiting plan is upgraded.

### 2. Actual BUY links signal as-of `buy_date`

`record_portfolio_buy()` no longer chooses a signal merely because it is actionable today.

Eligible linked signal must:

- have the same symbol;
- have `signal_date <= buy_date`;
- not be `INVALID_DATA`;
- not have terminated before `buy_date`;
- not be `EXPIRED` or `INVALIDATED_BEFORE_ENTRY` on the buy date itself.

A historical signal that is CLOSED today can still be valid for a backdated BUY if its terminal date is after the actual buy date.

If no historical signal is provably valid, the BUY is stored as unlinked instead of borrowing a future signal.

If the operator explicitly supplies `signal_id`, that ID must also pass the same as-of-buy-date validation. A future/invalid explicit ID fails closed.

### 3. Position Management technical data is causal as-of `analysis_date`

The canonical maintenance launcher now runs:

`modules/portfolio/position_management_runtime_integrity.py`

The wrapper caps historical candles to `Date <= analysis_date` before delegating to the existing `position_management_engine.technical_snapshot()` implementation.

Consequences:

- a newer/intraday candle cannot leak into an older maintenance run;
- current price, indicators, max-high-since-buy, and min-low-since-buy are all computed from the same causal dataset;
- the stable management decision rules are unchanged;
- the returned payload records `analysis_cutoff` for auditability.

If there is no candle on or before the requested analysis date, the technical snapshot fails closed with `NO_TECHNICAL_DATA_ON_OR_BEFORE_ANALYSIS_DATE`.

### 4. SELL never guesses between multiple OPEN lots

If `record_portfolio_sell()` is called using only a symbol:

- exactly one OPEN position -> that position may be closed;
- more than one OPEN position -> fail with `MULTIPLE_OPEN_POSITIONS_USE_POSITION_ID`;
- no OPEN position -> fail with `OPEN_PORTFOLIO_POSITION_NOT_FOUND`.

When multiple lots exist, the operator must supply `position_id`.

This prevents a SELL correction from accidentally closing the newest lot simply because it sorts last.

## Previously fixed related protections

The following protections predate this integrity patch and remain in force:

- future signal links on legacy portfolio positions are rejected/repaired;
- initial plan is frozen on actual BUY;
- changing `buy_date` re-resolves the initial plan as-of the corrected date;
- CLOSED positions can be corrected without reopening them;
- corrected CLOSED buy/sell facts recalculate `realized_return_pct`;
- portfolio edits are recorded in `portfolio_edit_audit`;
- machine signal lifecycle history is not rewritten by actual-portfolio corrections.

## Files intentionally changed/added

Core isolation:

- `modules/analytics/__init__.py`
- `modules/analytics/portfolio_integrity_overlay.py` (new)
- `modules/portfolio/position_management_runtime_integrity.py` (new)
- `maintenance/RUN_POSITION_MANAGEMENT.bat`

Verification/documentation:

- `tests/test_portfolio_maintenance_integrity.py` (new)
- `docs/PORTFOLIO_MAINTENANCE_INTEGRITY.md` (this document)

No scoring configuration, broker formula, Decision Engine, Exit Engine, Discovery Engine, market/sector model, or Telegram report format is changed by this patch.

## Regression coverage

The dedicated tests verify:

1. WAITING `PULLBACK` upgraded to confirmed `BREAKOUT` keeps `setup_type`, entry zone, SL, TP1, and TP2 coherent.
2. A backdated BUY selects the signal valid on the buy date rather than a newer active signal.
3. An explicitly supplied future `signal_id` is rejected.
4. SELL-by-symbol with two OPEN lots fails and requires `position_id`.
5. Technical maintenance run for 2026-08-24 ignores a 2026-08-26 candle.
6. Windows Position Management launcher uses the integrity runtime.

## Operational flow after this patch

Actual BUY:

`transaction facts -> as-of signal resolution -> portfolio_positions -> existing immediate initial-plan freeze`

Daily maintenance:

`last completed IDX session -> refresh OPEN symbols -> technical as-of cutoff -> existing broker history -> existing sector/market context -> unchanged management rules -> report`

Actual SELL:

`position_id OR unambiguous single-symbol OPEN lot -> realized return -> CLOSED`

Closed correction:

`position_id -> corrected transaction facts -> realized return recalculation -> audit trail`

## Non-goals

This patch deliberately does not:

- change BUY/SELL recommendations;
- change scores or thresholds;
- change TP/SL calculation formulas;
- create automatic order execution;
- rewrite lifecycle history;
- merge or split portfolio lots;
- change broker confirmation windows;
- change AI interpretation behavior.

Those areas require separate review and separate commits.
