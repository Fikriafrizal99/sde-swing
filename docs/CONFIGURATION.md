# Configuration

`config/pipeline.json` owns schedule-independent pipeline policy, thresholds,
outputs, and the protected Stage 2 decision policy. `config/data_sources.json`
owns providers and fallback/health policy. `.env` is the only place for local
secrets and is ignored; `.env.example` is blank.

AI Interpretation uses Groq for presentation-only narrative fields. Put the
secret only in local `.env` as `GROQ_API_KEY=...`. Optional overrides are
`GROQ_MODEL` (default `llama-3.3-70b-versatile`),
`GROQ_MAX_WATCHLIST_CALLS` (maximum 5), and `GROQ_CACHE_DIR`. Missing key,
timeout, malformed response, or quota failure falls back to deterministic SDE
text and never changes engine-owned decisions, prices, scores, or risk levels.

All active configuration and runtime provenance use version
`1.7.1`. Multi-Source remains a capability descriptor, not part of the semantic
version. `auto_entry_enabled` remains `false` and production stays
`MODERATE_BASELINE`.

