# Troubleshooting

- `NOT_CONFIGURED`: inspect source readiness and `ZAPI_IDX_BASE_URL` /
  `ZAPI_IDX_API_KEY`. The endpoint documentation is configured, but missing
  credentials intentionally keep ZAPI out of `LIVE` mode.
- `UNSUPPORTED`: the requested canonical type has no verified ZAPI endpoint
  or the documented response cannot satisfy its canonical semantics. Do not
  add a guessed path; use the configured fallback or leave the record absent.
- `ZAPI_RESPONSE_INVALID`: inspect endpoint shape, `data`/`Results`, and the
  documented pagination fields. A malformed response is rejected rather than
  mapped into a partial canonical record.
- `FILE`/`FALLBACK`: verify broker raw/summary date and schema. An empty
  Stockbit key does not block file-based processing.
- `SKIPPED` Final Watchlist: inspect predecessor status JSON for trade date,
  config version, and dependency freshness.
- Empty Post Market snapshot: the job returns `PARTIAL` and never claims
  `SUCCESS` with valid data.
- Telegram `NOT_CONFIGURED`: set bot/chat variables. Empty topic IDs safely use
  the main chat.
