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

## Historical report access boundary

Weekend/holiday access is intentionally split from normal engine execution:

```text
latest completed IDX session
  -> existing dated engine artifacts
  -> enhanced report builder
  -> current presentation
  -> preview-only OR delivery-only resend
```

Preview-only and resend paths do not run the engine or dependency graph. A
missing dated artifact is an explicit missing-artifact condition, not permission
to relabel newer live data as historical.

### Market Outlook recovery

A missing Market Outlook now has a separate point-in-time recovery path:

```text
RUN_MARKET_OUTLOOK.bat [9]
  -> tools/recover_market_outlook.py
  -> modules/global_market/historical_global_market_snapshot.py
  -> YahooGlobalMarketProvider.download_batch_range()
  -> existing global-market validator
  -> existing global sentiment scorer
  -> previous-session IHSG/technical context
  -> existing sector-rotation producer
  -> enhanced Market Outlook report builder
  -> preview/status only; Telegram OFF
```

This recovery path is deliberately separate from the normal live runner. The
normal Market Outlook command and behavior remain unchanged.

For a target trade date `T`, global instruments are grouped by the market
session that was already completed at the configured Market Outlook timestamp
(currently 07:30 Asia/Jakarta). Each Yahoo request is bounded to that expected
session, and the existing `validate_instrument()` and
`compute_global_sentiment()` functions remain authoritative.

The IHSG regime and technical/sector context are constrained to the previous
completed IDX session because a pre-market report for `T` must not consume the
IDX close of `T`. Current-state ZAPI activity is intentionally not queried in
historical recovery because this contract has no guaranteed point-in-time ZAPI
activity source.

Recovery records `HISTORICAL_AS_OF`, the as-of timestamp, prior technical
snapshot, and bounded Yahoo transport ranges. It fails closed when those
historical facts cannot be reconstructed. It does not execute or modify the
Decision Engine, Candidate Selector, Broker Fusion, Final Watchlist engine,
lifecycle semantics, or quant parameters.

### Post Market recovery

Post Market remains the other explicit missed-session recovery. It runs the
same frozen Post Market runtime for the latest completed trade date with
Telegram disabled; its historical evaluation time is pinned to the requested
session close.

Neither recovery path bypasses Final Watchlist same-date dependency validation.

See `RUNTIME_JOBS.md` for the operational recovery policy.

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
