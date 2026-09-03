# PULLBACK Logic Freeze — Testing Branch

**Freeze ID:** `PULLBACK_TESTING_20260903`  
**Effective date:** 2026-09-03  
**Branch:** `testing`  
**Baseline commit before freeze:** `a70a0b7402bab67f3de911546a1bb9381cc986c9`  
**Status:** **FROZEN**

## Decision

The current PULLBACK logic is frozen on the `testing` branch. The purpose is to preserve the current technical definition, thresholds, entry geometry, and risk/target contract while additional out-of-sample evidence is collected.

This is a change-control decision, not a declaration that PULLBACK is production-proven.

## Why PULLBACK is being frozen

The FTJ performance review dated 2026-09-03 shows that PULLBACK is currently the strongest setup in the evaluated sample:

| Metric | PULLBACK result |
| --- | ---: |
| Closed trades | 20 |
| Win rate | 80.0% |
| Average return | +3.70% |
| Median return | +4.83% |
| Profit factor | 4.79 |
| Average MFE | +6.88% |
| Average MAE | -2.76% |

The evidence is strong enough to justify preserving the current logic, but not strong enough to justify further optimization. The sample is still limited, the evaluated market-regime coverage is BULL-only, and the replay fidelity is `PRICE_LIFECYCLE_ONLY` rather than a full live-context replay.

Freezing now reduces the risk of **post-result tuning / overfitting**. New results should test the existing PULLBACK logic instead of continuously changing the rules after seeing outcomes.

## Frozen technical contract

A stock is classified as PULLBACK only after BREAKOUT priority and when the current technical conditions satisfy the existing contract:

- Trend context: `close > SMA20` and `SMA20 > SMA50`.
- Distance to EMA20: between `-2.0%` and `+3.0%`.
- RSI(14): between `45` and `68`.
- MACD histogram: `>= 0`.
- PULLBACK readiness trigger contribution: `20` points.
- Candidate minimum score: `58`.

The PULLBACK decision profile is also frozen:

| Parameter | Frozen value |
| --- | ---: |
| technical_min | 67 |
| strong_quality | 72 |
| composite_trigger | 64 |
| watch_score | 55 |
| readiness_ready | 64 |
| readiness_trigger_reference | 42 |
| volume_ratio_min | 0.80 |
| soft_extension | 2.20 ATR |
| hard_extension | 2.80 ATR |
| min_rr | 1.00R |
| preferred_rr | 2.00R |
| support_lookback | 30 sessions |
| resistance_lookback | 120 sessions |

## Frozen entry and risk geometry

For a valid PULLBACK plan, the current entry-zone calculation remains:

```text
entry_low  = EMA20 - 0.25 * ATR
entry_high = min(reference_close * 1.01, EMA20 + 0.35 * ATR)
planned_entry = entry_high
```

Risk and target calculations continue to use the planned entry rather than the current close. The existing support-based/ATR stop framework, minimum 1R target framework, preferred 2R framework, and resistance-above-entry requirement are part of the frozen economic contract.

## What is explicitly NOT frozen by this dedicated PULLBACK freeze

The dedicated freeze does **not** lock the Broker data lookback/source selection. In particular, research comparing **Broker 1D versus Broker 3D** may continue.

Broker research must be treated as an overlay/context experiment. It must not silently change the PULLBACK technical definition, thresholds, setup priority, or entry/SL/TP economics.

The following are also outside this dedicated freeze when they do not alter PULLBACK economics:

- Broker ingestion/fusion implementation.
- Reporting and Telegram/UI formatting.
- Data-source adapters.
- Observability/logging.
- Lifecycle-only fixes that do not alter entry, stop-loss, or target calculations.

## Machine enforcement

The freeze is recorded in:

`config/pullback_logic_freeze.json`

CI validates it through:

`tools/ci_validate_pullback_freeze.py`

The validator checks both the frozen PULLBACK configuration and the Git blob identity of the core implementation files that determine PULLBACK technical selection, decision behavior, entry validation, and baseline entry/risk calculations.

Any drift causes CI to fail with `PULLBACK FREEZE INVALID`.

## Change-control rule

Do **not** tune the current PULLBACK logic directly on `testing`.

Any proposed PULLBACK change must be developed on a separate experiment branch and accompanied by documented before/after evidence. The preferred evidence target is at least **30 new closed out-of-sample PULLBACK episodes**, with additional non-BULL regime coverage where available.

Those evidence targets do not automatically authorize a change. They are minimum evidence goals for deciding whether the freeze should be deliberately reopened.

## Current interpretation

**PULLBACK = PRIMARY SETUP / PROMISING EDGE, NOT YET PRODUCTION PROVEN.**

The correct next step is to accumulate new observations against this unchanged baseline while separately testing Broker 1D/3D context and other non-PULLBACK components.
