# Architecture

```text
run_sde_job.py / run_sde_job_integrated.py
  -> RunnerContext / RuntimeContext
  -> DataSourceManager
  -> SourceRouter -> canonical records -> quality/conflict checks
  -> snapshot builder -> analysis/decision adapters
  -> engine-owned artifacts
  -> enhanced report builder
  -> current presentation formatter
  -> ReportPayload
  -> TelegramRouter
  -> modules/job_runner/delivery.py
  -> unified status writer / delivery log
```

`DataSourceManager` is the provider readiness and routing facade. It returns
canonical records plus provenance and health metadata. The decision engine
remains the owner of trading thresholds and protected decision columns.

## Reporting boundary

Reporting is a downstream presentation layer. It may select, format, explain,
and route already-produced engine facts, but it must not recalculate or mutate:

- decision weights, thresholds or hard blockers;
- technical/broker/market scores;
- final decision status;
- entry zone, stop-loss, TP1/TP2 or risk/reward;
- canonical source ownership or validation outcome;
- lifecycle state-transition semantics;
- portfolio sizing/risk policy.

The active scheduled/resend reporting path is owned by:

- `modules/job_runner/enhanced_daily_reports.py`
- `modules/job_runner/enhanced_runtime_bridge.py`
- current presentation modules under `modules/telegram/`
- `modules/telegram/router.py`
- `modules/job_runner/delivery.py`

Current dedicated presentation modules are:

- `market_outlook_ui.py`
- `post_market_ui.py`
- `final_watchlist_ui.py`

Shared operational presentation can remain in `daily_report_ui.py` where it is
still used by active reports.

## Compatibility boundary

`master_pipeline.py` is a deprecated compatibility entry point retained for
historical Full Manual/regression contracts. Its remaining dependency on
`modules/telegram/telegram_bot.py` and
`modules/telegram/swing_report_builder.py` does not make those modules part of
the preferred operational reporting architecture.

New launchers, maintenance tools, previews, and documentation must use the
current integrated reporting/delivery path. Compatibility-only modules may be
removed only after their last caller is migrated and regression evidence proves
that engine-owned artifacts and locked quant behavior are unchanged.

See `TELEGRAM_ROUTING.md` for the current topic/routing contract.
