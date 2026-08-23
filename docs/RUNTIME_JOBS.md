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

### Market Outlook

`RUN_MARKET_OUTLOOK.bat` provides:

```text
[9] Recovery sesi terakhir terlewat - historical/as-of, tanpa Telegram
```

Recovery is implemented by `tools/recover_market_outlook.py`. It is separate
from the normal live runner, so the canonical normal command remains unchanged:

```text
run_sde_job_integrated.py --job market_outlook
```

The recovery path does **not** execute the Decision Engine, Candidate Selector,
Broker Fusion, Final Watchlist engine, or Telegram delivery.

For target trade date `T`, recovery does the following:

1. resolves the configured Market Outlook timestamp for `T` (currently 07:30
   Asia/Jakarta);
2. resolves the previous completed IDX session `T-1 session`;
3. loads the dated technical snapshot from `data/output/snapshots/<T-1>/` for
   breadth and sector context;
4. builds a global-market snapshot through
   `modules/global_market/historical_global_market_snapshot.py`;
5. groups each global instrument by the session that was actually completed at
   the historical as-of timestamp;
6. requests Yahoo only through that expected session via the bounded
   `download_batch_range()` transport;
7. runs the existing `validate_instrument()` freshness logic and existing
   `compute_global_sentiment()` formula without changing either formula;
8. refreshes IHSG history if possible, but calculates the regime only through
   the previous IDX session so the target day's close cannot leak into a
   pre-market report;
9. calculates sector rotation only from the previous dated technical snapshot;
10. refuses to query current-state ZAPI activity because no guaranteed
    point-in-time ZAPI activity endpoint is part of this recovery contract;
11. builds the report through the same enhanced Market Outlook presentation
    path and writes preview/status with Telegram disabled.

Recovery is fail-closed. It fails rather than substituting newer data when:

- the previous-session technical snapshot is missing or date-mismatched;
- point-in-time global coverage is below the configured minimum;
- IHSG regime cannot be calculated at the historical context date;
- required sector-rotation source facts cannot satisfy report validation.

An authentic live Market Outlook snapshot created on the target trading day may
be reused. A `LIVE` snapshot created after the target date is not accepted as a
historical recovery source because it may contain future information.

After recovery succeeds, use `Preview existing` first. `Kirim ulang` can send
that recovered report later without rerunning recovery or any engine.

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

### Final Watchlist

Final Watchlist recovery never bypasses its dependency contract. It may only be
produced when the required Market Outlook, Post Market/technical snapshot,
broker summary, and broker multi-day context for the same trade date are valid.

The Market Outlook historical recovery writes the same versioned status
contract for the requested trade date, with `recovery_mode=HISTORICAL_AS_OF` and
its as-of/context lineage recorded. `SUCCESS_WITH_WARNING` remains a dependency-
ready status under the existing dependency contract; the warning records that
current-state ZAPI activity was intentionally not queried.

If a required broker dependency never existed for that historical trade date,
the system must report the missing dependency instead of fabricating a
replacement or mixing dates.

## Example: Friday missed, weekend access

If the Friday session was `2026-08-21` and the application is opened on
Saturday/Sunday `2026-08-22` / `2026-08-23`:

1. Preview/resend resolves to `2026-08-21`, not the weekend date.
2. Existing reports for `2026-08-21` remain accessible.
3. If Friday Market Outlook was missed, Market Outlook option `[9]` reconstructs
   it at the Friday 07:30 historical/as-of boundary, using Thursday IDX
   technical/IHSG context and only global sessions knowable by that timestamp.
4. If Post Market was missed, Post Market option `[9]` can recover the Friday
   close-session pipeline without Telegram.
5. Both recovered reports should be inspected with `Preview existing` before
   any resend.
6. Final Watchlist remains unavailable until Market Outlook, Post Market, and
   all required Friday broker dependencies are valid for `2026-08-21`.

This policy preserves the audited quant/engine freeze while making weekend
operations usable for inspection, resend, and point-in-time-safe recovery.
