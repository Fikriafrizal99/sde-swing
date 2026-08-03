# Troubleshooting

- `NOT_CONFIGURED`: inspect source readiness and environment variables; this
  is expected for ZAPI until its documentation is supplied.
- `FILE`/`FALLBACK`: verify broker raw/summary date and schema. An empty
  Stockbit key does not block file-based processing.
- `SKIPPED` Final Watchlist: inspect predecessor status JSON for trade date,
  config version, and dependency freshness.
- Empty Post Market snapshot: the job returns `PARTIAL` and never claims
  `SUCCESS` with valid data.
- Telegram `NOT_CONFIGURED`: set bot/chat variables. Empty topic IDs safely use
  the main chat.

