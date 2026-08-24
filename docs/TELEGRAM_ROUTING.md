# Telegram routing

Telegram delivery in the active SDE Swing runtime is presentation-only. It must
never recalculate or mutate engine-owned decisions, scores, entry/exit levels,
or lifecycle semantics.

## Active delivery architecture

```text
engine-owned artifacts
  -> enhanced report builder
  -> current presentation formatter
  -> ReportPayload
  -> TelegramRouter
  -> modules/job_runner/delivery.py
  -> Telegram topic
```

The integrated runtime and resend tools use this path. Report generation is
separate from engine ownership; delivery failures do not authorize a decision
recalculation.

Final Watchlist AI is a separate downstream consumer. It starts only after the
canonical Final Watchlist entrypoint has completed successfully:

```text
official Final Watchlist -> Topic 9
  -> isolated Watchlist AI runner
  -> provider failover / validation
  -> ReportPayload
  -> TelegramRouter
  -> dedicated AI topic
```

A Watchlist AI/provider/delivery failure never changes the official Final
Watchlist result or exit code.

## Credentials

Credentials are resolved from the local runtime configuration/environment. Keep
secrets outside Git-tracked source files.

Primary names:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The isolated Watchlist AI topic uses:

- `TELEGRAM_THREAD_AI_ID`

Topic configuration can be stored in local `config/telegram.json` through
`tools/telegram_settings.py`, with `config/scheduler.json` as the repository
fallback for active report routes. Watchlist AI deliberately has no repository
fallback topic ID until a dedicated numeric AI topic is configured.

## Categories

`TelegramRouter` recognizes five categories:

- `SIGNAL` — Final Watchlist and Signal Detail family.
- `REPORT` — Market Outlook, Post Market, broker reports, portfolio/lifecycle
  reporting and other operational reports.
- `SYSTEM` — startup, source-health, warning, config and dependency messages.
- `NEWS` — Morning News, Post Market News and isolated news delivery.
- `AI` — isolated Final Watchlist AI interpretation and its informational status
  messages only.

Concrete report routes have priority over legacy generic category environment
variables. In particular, a configured Final Watchlist/Signal Detail route must
not be overridden by `TELEGRAM_THREAD_SIGNAL_ID`.

Watchlist AI never reuses `NEWS`, `SIGNAL`, or generic `REPORT` routes. Its
caller refuses main-chat fallback if `TELEGRAM_THREAD_AI_ID` is not configured.

## Repository route map

The current scheduler fallback is:

| Report family | Topic ID |
|---|---:|
| Market Outlook | 9 |
| Post Market + heatmap | 9 |
| Broker Summary / CSV | 9 |
| Final Watchlist Summary | 9 |
| Final Watchlist Detail | 9 |
| Final Watchlist CSV | 9 |
| Signal Detail | 6 |
| Evaluation / generic report | 701 |
| System | 5 |
| News | 1451 |
| Watchlist AI | `TELEGRAM_THREAD_AI_ID` (local env; required) |

`final_watchlist_summary`, `final_watchlist_detail`, `final_watchlist_csv`, and
`final_watchlist` are one operational family and must remain on the same Final
Watchlist topic unless a future explicit contract changes them together.

## Fallback rules

A missing concrete route is considered unresolved by `TelegramRouter`.
`modules/job_runner/delivery.py` may then apply the explicit scheduler fallback
for the payload report type/topic. NEWS delivery is intentionally isolated by
its dedicated callers and must not silently fall back to the main chat.

Watchlist AI is stricter: `tools/run_watchlist_ai.py` checks the resolved route
before calling the common delivery layer. If no numeric dedicated AI topic is
available, it returns `SKIPPED_AI_TOPIC_NOT_CONFIGURED` and sends nothing. This
prevents AI interpretations or AI-failure notices from leaking into main chat,
News/IDX, Signal, or operational report topics.

The legacy `TELEGRAM_THREAD_SIGNAL_ID` is compatibility-only. It is not the
source of truth for current Final Watchlist or Signal Detail routing.

## Watchlist AI failure notification

`watchlist_ai.notify_on_failure=true` enables an informational message when all
configured AI providers fail for one or more symbols, or when the isolated AI
subsystem cannot consume the official artifact. The message is routed only to
the dedicated AI topic and explicitly states that the official Final Watchlist
remains unchanged.

Provider failure is an AI-subsystem state; it is not an SDE engine failure and
must not trigger Final Watchlist recalculation, rollback, or scheduler retry.

## Delivery contract

Every active delivery path must preserve:

- HTML escaping for dynamic values;
- bounded message length and safe splitting;
- report-type idempotency;
- force-resend semantics;
- attachment routing (`sendDocument` / `sendPhoto`) to the same logical topic;
- delivery logs with report type, route, message ID and status;
- lifecycle acknowledgement only for visibly delivered events.

Watchlist AI additionally preserves per-symbol idempotency by using a distinct
interpretation report type per ticker while all messages retain the
`watchlist_ai` topic label.

## Active versus compatibility-only Telegram code

The production/integrated sender is `modules/job_runner/delivery.py`.
`modules/telegram/telegram_bot.py` and `modules/telegram/swing_report_builder.py`
remain only because the deprecated `master_pipeline.py` compatibility entry
point still references them for historical Full Manual/regression contracts.
They are not the preferred operational/reporting path and must not be used by
new launchers, maintenance tools, or documentation.

The maintenance Telegram connectivity check uses:

```bat
python tools\telegram_settings.py validate-credentials
python tools\telegram_settings.py test-all
```

Do not remove compatibility-only files until their remaining caller has been
migrated and regression evidence proves the engine/output contract unchanged.
