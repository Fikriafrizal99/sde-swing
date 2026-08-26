# Post Market Delivery Anti-Duplicate Guard

Date introduced: 2026-08-26  
Scope: canonical market-first Post Market Telegram delivery only  
Baseline before this patch: `testing@1b16d7f`

## Problem

A Post Market run can produce two Telegram payloads: the standalone heatmap and the Post Market text. The shared delivery layer uses durable SQLite idempotency, but Telegram itself does not provide an idempotency token.

There is therefore an unavoidable crash/finalization window:

1. Telegram accepts the Post Market text and returns a `message_id`.
2. Local delivery finalization fails before the idempotency state is safely committed as `SENT`.
3. The scheduler sees `DELIVERY_FAILED` and retries the Post Market job.
4. Heatmap may already be duplicate-suppressed while the Post Market text is considered retryable.
5. Result: one heatmap but two identical Post Market text messages.

## Fix

The canonical `run_sde_job_integrated_market_first.py` entrypoint now wraps the shared delivery function with `deliver_post_market_hardened()`.

The guard is deliberately narrow:

- only `report_type=POST_MARKET` is hardened;
- the shared `modules/job_runner/delivery.py` implementation is unchanged;
- scheduler retry rules are unchanged;
- heatmap behavior is unchanged;
- report content is unchanged;
- trading, scoring, broker, decision, exit and portfolio logic are unchanged.

## Rule

A Post Market delivery is considered remotely accepted only when Telegram returned message IDs for **all expected outbound parts**.

If local finalization then reports `FAILED` or `DELIVERY_STATE_UNCERTAIN`:

- the result is converted to terminal `DELIVERY_STATE_UNCERTAIN`;
- `remote_acceptance_confirmed=true` is recorded;
- automatic retry is suppressed;
- the SQLite idempotency state is sealed as `SENT` so a later normal rerun is duplicate-suppressed;
- the attempt row remains auditable as `SENT_UNCERTAIN`.

If Telegram failed before returning a message ID, the failure stays `FAILED` and remains retryable.

If a multi-part Post Market message was only partially accepted, it also stays `FAILED`; the guard does not pretend an incomplete delivery was successful.

## Operational behavior

Expected normal case:

`heatmap SENT -> Post Market SENT -> state SENT`

Remote-ACK ambiguity case:

`Post Market Telegram ACK -> local finalization error -> DELIVERY_STATE_UNCERTAIN -> retry suppressed -> idempotency sealed`

Pre-send/network failure case:

`Telegram no ACK -> FAILED -> normal scheduler retry remains allowed`

## Files

- `modules/job_runner/post_market_delivery_guard.py`
- `run_sde_job_integrated_market_first.py`
- `tests/test_post_market_delivery_guard.py`
- `docs/POST_MARKET_DELIVERY_ANTI_DUPLICATE.md`

## Regression coverage

The tests cover:

1. Complete one-part remote ACK followed by local failure is sealed and duplicate-suppressed.
2. Failure before any Telegram ACK remains retryable.
3. Partial multi-part delivery is not sealed.
4. Market-first shim activates the hardened Post Market delivery wrapper.

## Non-goals

This patch does not claim exactly-once delivery at the Telegram API boundary. A process can theoretically die after Telegram accepts a message but before Python receives or records the response. No local-only implementation can eliminate that remote ambiguity completely without Telegram-side idempotency support.

The patch closes the observed and recoverable case where Telegram acceptance is already evidenced by returned `message_id` values but local finalization fails afterward.
