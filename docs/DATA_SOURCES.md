# Data sources

`config/data_sources.json` is the source of truth for ownership, priority,
fallback policy, freshness, and readiness.

- ZAPI IDX is `NOT_CONFIGURED` until documentation, base URL, and API key are
  all present. Forced/offline runs are labelled `MOCK`, never `LIVE`.
- Stockbit API is optional. An empty key leaves the API `NOT_CONFIGURED` and
  allows CSV/JSON/Tampermonkey/local broker raw file fallback.
- Historical provider is file fallback for existing daily history.

Every routed record carries canonical provenance and the manager emits source
health and coverage telemetry.

