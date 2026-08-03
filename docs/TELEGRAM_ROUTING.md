# Telegram routing

Credentials are read only from `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
Topic IDs are read from `TELEGRAM_THREAD_SIGNAL_ID`,
`TELEGRAM_THREAD_REPORT_ID`, and `TELEGRAM_THREAD_SYSTEM_ID`.

`TelegramRouter` classifies final watchlist/final decision/signal detail as
SIGNAL; market, post-market, broker and multi-day reports as REPORT; startup,
warnings, health, and dependency/config errors as SYSTEM. An empty thread ID
uses the main chat. Delivery records include report type, target thread,
message thread ID, message ID, part count, idempotency key and status.

