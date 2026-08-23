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

## Credentials

Credentials are resolved from the local runtime configuration/environment. Keep
secrets outside Git-tracked source files.

Primary names:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Topic configuration can be stored in local `config/telegram.json` through
`tools/telegram_settings.py`, with `config/scheduler.json` as the repository
fallback for active report routes.

## Categories

`TelegramRouter` recognizes four categories:

- `SIGNAL` — Final Watchlist and Signal Detail family.
- `REPORT` — Market Outlook, Post Market, broker reports, portfolio/lifecycle
  reporting and other operational reports.
- `SYSTEM` — startup, source-health, warning, config and dependency messages.
- `NEWS` — Morning News, Post Market News and isolated news delivery.

Concrete report routes have priority over legacy generic category environment
variables. In particular, a configured Final Watchlist/Signal Detail route must
not be overridden by `TELEGRAM_THREAD_SIGNAL_ID`.

## Repository route map

The current scheduler fallback is:

| Report family | Topic ID |
|---|---:|
| Market Outlook | 9 |
| Post Market + heatmap | 9 |
| Broker Summary / CSV | 9 |
| Broker Multi-Day / CSV | 9 |
| Final Watchlist Summary | 9 |
| Final Watchlist Detail | 9 |
| Final Watchlist CSV | 9 |
| Signal Detail | 6 |
| Evaluation / generic report | 701 |
| System | 5 |
| News | 1451 |

`final_watchlist_summary`, `final_watchlist_detail`, `final_watchlist_csv`, and
`final_watchlist` are one operational family and must remain on the same Final
Watchlist topic unless a future explicit contract changes them together.

## Fallback rules

A missing concrete route is considered unresolved by `TelegramRouter`.
`modules/job_runner/delivery.py` may then apply the explicit scheduler fallback
for the payload report type/topic. NEWS delivery is intentionally isolated by
its dedicated callers and must not silently fall back to the main chat.

The legacy `TELEGRAM_THREAD_SIGNAL_ID` is compatibility-only. It is not the
source of truth for current Final Watchlist or Signal Detail routing.

## Delivery contract

Every active delivery path must preserve:

- HTML escaping for dynamic values;
- bounded message length and safe splitting;
- report-type idempotency;
- force-resend semantics;
- attachment routing (`sendDocument` / `sendPhoto`) to the same logical topic;
- delivery logs with report type, route, message ID and status;
- lifecycle acknowledgement only for visibly delivered events.

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
