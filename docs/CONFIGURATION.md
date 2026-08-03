# Configuration

`config/pipeline.json` owns schedule-independent pipeline policy, thresholds,
outputs, and the protected Stage 2 decision policy. `config/data_sources.json`
owns providers and fallback/health policy. `.env` is the only place for local
secrets and is ignored; `.env.example` is blank.

All active configuration and runtime provenance use version
`1.7.0-multisource`. `auto_entry_enabled` remains `false` and production stays
`MODERATE_BASELINE`.

