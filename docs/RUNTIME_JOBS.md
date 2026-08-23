# Runtime jobs

The integrated registry contains `pre_market`, `market_outlook`, `post_market`,
`technical_snapshot`, `broker_summary`, `broker_multi_day`,
`universe_selection`, `candidate_selection`, `final_watchlist`,
`final_decision`, `telegram_delivery`, and `job_status`.

Final Watchlist requires current, successful status records for market outlook,
post market/technical snapshot, broker summary, and broker multi-day. The
dependency validator rejects missing, stale trade dates, or mismatched config
versions.

## Trading-day guard

Normal engine execution remains trading-calendar gated. A normal
`market_outlook`, `post_market`, or `final_watchlist` run without an explicit
historical trade date must not turn a weekend or IDX holiday into a synthetic
trading session.

The calendar guard belongs to engine execution only. Operator access to
existing reports is allowed on weekends and holidays through artifact-only
preview/resend paths.

## Weekend / holiday report access

The Windows launchers resolve the latest completed IDX session through
`tools/resolve_last_trading_day.py`.

`Preview existing` is read-only with respect to the SDE engine:

```text
Market Outlook  -> tools/resend_daily_report.py --job market_outlook --preview-only
Post Market     -> tools/resend_daily_report.py --job post_market --preview-only
Final Watchlist -> tools/resend_final_watchlist.py --preview-only
```

These preview paths:

- do not run the engine;
- do not refresh Yahoo/Zapi/broker data;
- do not rerun the dependency graph;
- do not send Telegram;
- rebuild only the current presentation from already-existing, dated artifacts;
- fail with `*_ARTIFACT_NOT_FOUND` when the requested session was never produced.

`Kirim ulang` uses the same dated artifacts but enables Telegram delivery. It
remains delivery-only and must not mutate engine status or decision artifacts.

## Missed-session recovery policy

A missed trading session is not treated the same as an existing report.
Recovery is stage-specific so historical output cannot gain look-ahead data.

### Post Market

`RUN_POST_MARKET.bat` provides:

```text
[9] Recovery sesi terakhir terlewat - jalankan Post Market tanpa Telegram
```

The launcher resolves the latest completed trading session and runs the same
current Post Market runtime with an explicit `--trade-date` and
`--no-telegram`.

This does not change Technical Engine formulas, candidate thresholds, scoring,
or decision policy. For an explicit historical trade date,
`_post_market_evaluation_datetime()` pins the downloader evaluation timestamp
to that session's configured market close. The canonical DailyBar boundary then
materializes data against the expected closed date before the frozen Technical
Feature Engine receives it.

Recovery should be followed by `Preview existing`; Telegram can then be sent
with `Kirim ulang` only after the recovered artifact has been inspected.

### Market Outlook

A Market Outlook that was never generated for a past session is **not**
reconstructed after the fact.

The global-market live snapshot fetcher uses information available at fetch
time. Rebuilding a Friday pre-market outlook on Sunday could therefore include
market closes that were not yet known before Friday's IDX session. That would
create look-ahead bias.

For this reason:

- existing historical Market Outlook artifacts may be previewed/resend;
- a missing historical Market Outlook returns `MARKET_OUTLOOK_ARTIFACT_NOT_FOUND`;
- the runtime must not silently create a historical Market Outlook from newer
  live data.

### Final Watchlist

Final Watchlist recovery never bypasses its dependency contract. It may only be
produced when the required Market Outlook, Post Market/technical snapshot,
broker summary, and broker multi-day context for the same trade date are valid.

If one of those required historical dependencies never existed, the system
must report the missing dependency instead of fabricating a replacement or
mixing dates.

## Example: Friday missed, weekend access

If the Friday session was `2026-08-21` and the application is opened on
Saturday/Sunday `2026-08-22` / `2026-08-23`:

1. Preview/resend resolves to `2026-08-21`, not the weekend date.
2. Existing reports for `2026-08-21` remain accessible.
3. If Post Market was missed, option `[9]` can recover the Friday Post Market
   without Telegram using the same frozen engine and Friday close-time context.
4. If Friday Market Outlook was never generated, it stays explicitly missing;
   the system does not backfill it with weekend knowledge.
5. Final Watchlist remains unavailable unless every required Friday dependency,
   including broker context, is valid for `2026-08-21`.

This policy preserves the audited quant/engine freeze while making weekend
operations usable for inspection, resend, and safe recovery where historical
reconstruction is deterministic.
