# SDE Stabilization Baseline — Commit 1

> Archived historical governance evidence. Retained for audit traceability; it
> is not current operational guidance. See `docs/README.md` for active documentation.

## Purpose

This document establishes the characterization baseline for
`audit/sde-stabilization`. It is derived from `docs/archive/v1.7/SDE_AUDIT_BASELINE.md` and
does not redefine the audit.

The stabilization branch starts from the audited baseline:

- repository: `Fikriafrizal99/sde-swing`
- audited base commit: `121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`
- implementation branch: `audit/sde-stabilization`
- package/config version: `1.7.0-multisource`
- production profile: `MODERATE_BASELINE`
- approved operation: supervised/shadow analysis
- auto-entry: `false`

## Stabilization scope

Only the open P0 -> P1 findings from the audit baseline are in scope.

Planned commit sequence:

1. audit baseline + quant freeze
2. artifact integrity
3. lifecycle consistency
4. runtime/status/locking
5. canonical data path
6. CI/release cleanup

P2 findings, new providers, UI enhancements, performance tuning, strategy
calibration, and quant optimization are outside this stabilization sequence.

## Quant freeze contract

Commit 1 does not modify production decision behavior. The protected contract
is captured in `config/audit_quant_freeze.json` and checked by
`tools/ci_validate_quant_freeze.py`.

The following remain frozen unless a later separately approved quant change is
made after stabilization:

- `MODERATE_BASELINE`
- decision weights and score construction
- candidate/setup thresholds
- decision thresholds
- hard blockers
- broker/foreign/market production adjustments
- closed-candle policy
- liquidity and microstructure penalties
- risk/reward thresholds
- entry calculation
- stop-loss calculation
- TP1/TP2 price calculation
- portfolio sizing/risk parameters
- `auto_entry_enabled=false`

The only scoped behavioral exception is Commit 3: lifecycle semantics may be
unified where the audit proves different interpretations across exit engine,
outcome tracker, database, shadow evaluation, or backtest. That exception does
not authorize recalculating entry, SL, TP1, or TP2 prices.

## Quant evidence at audited base

The freeze records both configuration-backed values and code-backed defaults.
This is intentional: freezing only `config/pipeline.json` would not detect
drift in production defaults resolved from
`modules/decision_engine/moderate_profiles.py`.

Baseline blob evidence:

- `config/pipeline.json`: `31dcff3f80d9fb0a644b539b5bd2b30e9bf99e42`
- `modules/decision_engine/moderate_profiles.py`: `9058c55b635ea8e49f8b5b074312371a321c04b9`
- `modules/decision_engine/smart_selective_v162.py`: `05fd29e596efa2ed08c7b8168b27735692488c3b`
- `modules/entry_plan_validator/validator.py`: `cbcb5e751d87e52137aa211bf50404e7fc658173`
- `modules/candidate_selector/technical_candidate_selector.py`: `3e9e77d11eb520e3ebed3b0beffbbc9e726ccfdb`
- `modules/technical_feature_engine/technical_feature_engine.py`: `d95f86f212ebe61823c54c53c2e9732a7763df12`
- `modules/broker_fusion/broker_fusion.py`: `d2e737200201dcb588971106f840fe9fa686b06d`

Lifecycle-module blob SHAs are evidence anchors, not immutable source hashes,
because Commit 3 is explicitly allowed to unify lifecycle semantics:

- `modules/exit_engine/exit_engine.py`: `422dfc663a3b8d4c26131c10bdf13daed32dad4c`
- `modules/analytics/outcome_tracker.py`: `6ef2b12800e6efcac9dd7b47e8c85ad405c974cc`
- `modules/database/swing_history_db.py`: `e380bafcbf1807f608ea6eeafd0b4e74730760d4`

## Current CI characterization

Current GitHub Actions evidence for audited base `121bc58`:

- workflow: `SDE Swing CI`
- run ID: `31626564477`
- job ID: `94214325196`
- Python: `3.12.13`
- compile step: PASS
- pytest: **27 failed, 510 passed, 3 subtests passed**
- overall job conclusion: FAILURE

This replaces the historical `28 failed / 492 passed` count only for current
failure inventory purposes. It does not change the audit verdict. No failing
test is fixed in Commit 1.

### Current failing tests

1. `tests/test_active_portfolio_hardening.py::test_interpretation_reflects_guardrail_state_and_broker_context`
2. `tests/test_broker_period_flow_continuation.py::test_final_watchlist_card_shows_period_intelligence_from_fixture`
3. `tests/test_broker_period_primary_pulse.py::test_broker_period_primary_uses_change_pulse_and_multiday_accumulation`
4. `tests/test_enhanced_daily_reports.py::test_final_report_contains_match_method`
5. `tests/test_enhanced_daily_reports.py::test_post_market_report_contains_50_20_match`
6. `tests/test_enhanced_daily_reports.py::test_missing_screening_fields_are_na`
7. `tests/test_final_watchlist_interpretive_reason.py::test_package_level_interpretive_reason_for_each_status`
8. `tests/test_final_watchlist_presentation.py::test_role_of_each_section_and_signal_block_use_exact_columns`
9. `tests/test_final_watchlist_presentation.py::test_price_risk_rendering_uses_idx_tick_rounding`
10. `tests/test_final_watchlist_presentation.py::test_insufficient_broker_data_is_not_rendered_as_neutral`
11. `tests/test_final_watchlist_presentation.py::test_buy_on_trigger_followup_labels_are_pending`
12. `tests/test_news_market_impact.py::test_global_event_can_override_ticker_no_match`
13. `tests/test_portfolio_broker_period_integrity.py::test_builder_uses_canonical_period_row_and_runtime_injects_portfolio_fields`
14. `tests/test_portfolio_management_reporting.py::test_management_output_compact_sections`
15. `tests/test_portfolio_management_reporting.py::test_management_output_one_day_flow`
16. `tests/test_portfolio_management_reporting.py::test_management_output_three_day_flow`
17. `tests/test_portfolio_runtime_guardrails.py::test_runtime_fails_closed_on_broker_date_conflict`
18. `tests/test_portfolio_runtime_guardrails.py::test_topic_config_is_strict_for_portfolio_report`
19. `tests/test_post_market_current_contract.py::test_current_box_uses_section_hierarchy_and_minimum_screening_contract`
20. `tests/test_post_market_current_contract.py::test_current_box_does_not_export_stale_ihsg_when_market_pulse_is_invalid`
21. `tests/test_post_market_market_pulse_ui.py::test_post_market_market_pulse_uses_factual_us_inputs_without_macro_event_rows`
22. `tests/test_post_market_market_pulse_ui.py::test_post_market_market_pulse_renders_neutral_inputs_as_separate`
23. `tests/test_post_market_market_pulse_ui.py::test_post_market_market_pulse_refuses_stale_or_nonfinite_inputs`
24. `tests/test_swing_v1_2.py::test_incremental_yahoo_refresh_by_symbol_same_day`
25. `tests/test_telegram_router_news.py::test_news_routed_to_news_topic`
26. `tests/test_v170_telegram_contract.py::test_final_watchlist_card_contains_entry_and_risk_fields`
27. `tests/test_zapi_end_to_end.py::test_zapi_end_to_end_offline`

These failures must be classified and closed by the owning stabilization
commit or by Commit 6 release cleanup. Tests must not be made green by changing
frozen quant behavior.

## Commit ownership boundaries

### Commit 2 — artifact integrity

Owns the P0 shared `FINAL_DECISION_V2.csv` writer problem: serialization,
atomicity, run-scoped evidence, hashes, and lineage. It must not change fusion
or decision semantics.

### Commit 3 — lifecycle consistency

Owns TP1/TP2/trailing/same-candle/max-hold/outcome interpretation consistency.
Price calculations remain frozen.

### Commit 4 — runtime/status/locking

Owns engine versus delivery/resend status separation, interrupt
terminalization, exit-code consistency, cleanup, and runtime lock behavior.
Shared V2 artifact integrity remains owned by Commit 2.

### Commit 5 — canonical data path

Owns conflict-date fail-closed enforcement and production routing through the
canonical data boundary. Equivalent valid inputs must preserve frozen quant
outputs.

### Commit 6 — CI/release cleanup

Owns final regression cleanup and release evidence. It is not a catch-all
architecture commit and may not silently change quant to obtain a green suite.

## Commit 1 acceptance

Commit 1 is complete when:

- branch ancestry remains rooted at audited `121bc58`;
- the audit scope is recorded;
- the current CI failure inventory is recorded;
- configuration-backed and code-backed production quant values are frozen;
- the freeze validator passes on the audited contract;
- deliberate quant drift is rejected by regression test;
- no production quant/runtime source is changed by Commit 1.

A new release verdict is not issued at this stage. The audit baseline remains
AMBER/RED and autonomous execution remains NO-GO until the stabilization
sequence is completed and re-audited.
