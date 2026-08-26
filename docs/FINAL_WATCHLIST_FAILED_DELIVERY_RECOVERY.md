# Final Watchlist Failed-Delivery Recovery

Date introduced: 2026-08-26  
Baseline before this patch: `testing@007dfaa`  
Scope: Final Watchlist preview/resend presentation layer only

## Problem

Normal Final Watchlist exact resend intentionally selects only a source run that
was fully delivered to Telegram. That is correct for ordinary replay because it
uses Telegram `copyMessage` and immutable archive fallback.

A different failure mode occurs when:

1. Final Watchlist generation succeeds and immutable previews are archived.
2. Telegram delivery fails before Telegram returns any `message_id` (for example,
   DNS resolution failure).
3. The source run therefore has only `FAILED` delivery rows.
4. `Preview Existing` cannot select it because the source was never `SENT`.
5. The previous selection may still point to an older trade date, so resend is
   correctly blocked by the date lock.

The 2026-08-26 incident matched this case: the report bundle existed, but Telegram
never acknowledged any Final Watchlist message.

## Recovery rule

`RUN_FINAL_WATCHLIST.bat` now routes Preview and Kirim Ulang through
`tools/resend_final_watchlist_recovery.py`.

The wrapper keeps the existing delivered-source path unchanged. It uses recovery
only when no fully delivered Final Watchlist source exists for the requested date.

A failed source is recoverable only when all of these conditions are true:

- source trade date equals the requested trade date;
- source job is `final_watchlist` or `full_manual`;
- canonical Final Watchlist summary exists;
- every selected canonical delivery row is `FAILED`;
- **no selected row contains any Telegram `message_id`**;
- the run-scoped preview manifest exists;
- manifest state is `PREPARED` or `DELIVERY_INCOMPLETE`, never delivered;
- each selected payload has an immutable run-scoped preview;
- preview SHA-256 matches the manifest;
- every source attachment has an immutable archived copy;
- attachment SHA-256 matches the manifest;
- exact Telegram part definitions exist in the manifest.

If any Telegram acknowledgement exists, recovery fails closed with
`FINAL_WATCHLIST_RECOVERY_BLOCKED_PARTIAL_TELEGRAM_ACK` to avoid duplicate sends.

## Replay behavior

Delivered source:

`Preview Existing -> delivered source -> copyMessage first -> hash-locked fallback`

Failed/unsent recovery source:

`Preview Existing -> FAILED source with zero Telegram ACK -> hash-lock selection -> direct archived exact replay`

Recovery does not run:

- Decision Engine
- Exit Engine
- Discovery
- broker scoring/formula
- technical engine
- report formatter
- mutable `LATEST` artifacts

The exact archived bytes/text approved during Preview are reused.

## Selection safety

Recovery uses a separate date-locked selection receipt:

`data/state/scheduler/final_watchlist_recovery_selection.json`

The receipt stores source run ID, trade date, delivery signature, selected paths,
and SHA-256 hashes. Any date mismatch, source drift, or file hash change aborts
before Telegram send.

When Preview finds a normal fully delivered source, the stale recovery selector
is cleared and the established exact-delivery selector remains authoritative.

## Changed paths

- `modules/job_runner/final_watchlist_recovery.py`
- `tools/resend_final_watchlist_recovery.py`
- `RUN_FINAL_WATCHLIST.bat`
- `tests/test_final_watchlist_failed_delivery_recovery.py`
- `docs/FINAL_WATCHLIST_FAILED_DELIVERY_RECOVERY.md`

## Non-goals

This patch does not change normal Final Watchlist generation or normal delivery.
It does not attempt to automatically repair a partially delivered Telegram
bundle. Partial acknowledgement remains a manual-investigation case because an
automatic whole-bundle retry could duplicate messages that Telegram already
accepted.
