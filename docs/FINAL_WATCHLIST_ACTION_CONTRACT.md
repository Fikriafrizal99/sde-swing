# Final Watchlist Action Contract

Status: canonical presentation contract for the action line on Final Watchlist detail cards.

## Purpose

The Final Watchlist card must keep execution guidance connected to the engine-owned trade plan. A technical support/resistance level may be shown as context, but presentation must not silently promote that level into an entry trigger.

This contract exists to prevent cards such as:

```text
Entry 3.990–4.070
R 4.190
Tunggu break >4.190
```

when no engine/source artifact actually declares `4.190` as the entry trigger.

## Ownership boundary

This is presentation only. It must not change:

- Decision Engine decisions, scores, thresholds, vetoes, or ranking.
- Entry-zone calculation.
- Stop loss, TP1, TP2, or risk-reward calculation.
- Technical support/resistance calculation.
- Broker/foreign-flow calculations.
- Lifecycle trigger/expiry semantics.

The Final Watchlist formatter only explains already-produced facts.

## Canonical action-source precedence

`modules/telegram/daily_report_ui.py` must resolve the action line in this order:

1. explicit `trigger_description` / equivalent engine-source trigger;
2. explicit `waiting_triggers` / pending execution conditions;
3. position versus the engine-owned entry zone;
4. generic wait-for-valid-trigger fallback when no executable detail is available.

Technical `support` and `resistance` are never fallback trigger sources.

## Entry-zone fallback

When there is no explicit trigger artifact:

- price above the entry zone -> tell the operator not to chase and wait for pullback to the entry zone;
- waiting state with a valid entry zone -> tell the operator to wait for a valid trigger in that entry zone;
- non-waiting state with a valid entry zone -> state that entry is only in that zone;
- no usable zone -> generic setup/trigger guidance.

This keeps the visible action tied to the same entry zone shown on the card.

## Explicit breakout triggers

A breakout/close-above instruction may appear only when an existing engine/source field explicitly says so, for example through `trigger_description` or `waiting_triggers`.

Generic machine-state identifiers such as `WAIT_FOR_ENTRY_TRIGGER`, `WAIT_FOR_ENTRY_ZONE`, and `ENTRY_NOT_TRIGGERED` are not executable trigger descriptions. The formatter must not expose those raw codes as user-facing actions; it skips them and continues to the next explicit condition or the entry-zone fallback.

The formatter must not derive `break > resistance` merely from:

```text
phase = WAIT / NOT READY / CONDITIONAL
+
resistance exists
```

That inference is prohibited because resistance is technical context, not automatically the execution trigger.

## S/R display

The card may continue to show:

```text
S <support> | R <resistance>
```

These levels remain useful technical context. Their presence alone has no execution meaning.

## Regression protection

`tests/test_final_watchlist_interpretive_reason.py` guards this contract, including a TINS-style regression where:

- current price is inside `3.990–4.070`;
- technical resistance is `4.190`;
- no explicit breakout trigger exists;
- the action must reference `3.990–4.070`, not invent `break >4.190`.

The tests also verify that an explicit engine/source trigger takes precedence when one is actually present.

## Change control

Future changes to the Final Watchlist action line must:

1. preserve engine/quant ownership;
2. never infer an execution trigger from S/R alone;
3. prefer explicit trigger artifacts over presentation heuristics;
4. keep fallback guidance connected to the visible entry zone;
5. update the regression tests and this contract when semantics intentionally change.
