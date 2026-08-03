# Data sources

`config/data_sources.json` is the source of truth for ownership, priority,
fallback policy, freshness, readiness, and the verified endpoint capabilities.

## ZAPI IDX

The adapter uses the environment variables `ZAPI_IDX_BASE_URL` and
`ZAPI_IDX_API_KEY`. The documented deployment value for the base URL is
`https://api.zpi.web.id/v1/finance:idx`; authentication is the `x-api-key`
header. The published contract documents a 60-request/minute free-tier limit,
which is handled as a retryable 429 response.

Only these paths are routed:

- `DailyBar` → `/stock-summary` (`length`, `start`, `date`, `code`)
- `MarketIndex` → `/index-summary` (`length`, `start`, `date`)
- `SymbolMetadata` → `/companies` and `/securities` (`length`, `start`, `code`;
  securities also accepts `sector` and `board`)
- `TradingStatus` → `/market-activity` (`type=suspend|relisting|uma`)

`/broker-summary` is documented and its response is covered by a fixture, but
it is not enabled as `BrokerFlow`: the response is aggregate per-broker
turnover and does not contain the symbol-level BUY/SELL/net fields required by
the canonical broker schema. `IntradayQuote`, `OrderBookSnapshot`,
`ForeignFlow`, and `CorporateAction` remain `UNSUPPORTED` or
`NOT_CONFIGURED`; no guessed URL is called.

The endpoint examples expose `{data, ...}` while the reference also describes
an optional `{status, message, content}` envelope. The client accepts both,
validates the expected list/activity shape and pagination fields, and records
the endpoint in canonical field provenance. Missing credentials produce
`NOT_CONFIGURED`/`MOCK`, never `LIVE`.

## Other providers

- Stockbit API is optional. An empty key leaves the API `NOT_CONFIGURED` and
  allows CSV/JSON/Tampermonkey/local broker raw file fallback.
- Historical provider is file fallback for existing daily history.

Every routed record carries canonical provenance and the manager emits source
health and coverage telemetry.
