# Legacy file manifest

| Class | Examples | Treatment |
|---|---|---|
| ACTIVE | `run_sde_job.py`, `modules/job_runner/`, `modules/data_sources/` | Official runtime and source layer |
| LEGACY_REQUIRED | `master_pipeline.py`, Stage 1/2 engines | Kept for compatibility and regression tests |
| COMPATIBILITY_ONLY | `modules/telegram/telegram_bot.py`, `modules/telegram/swing_report_builder.py`, `modules/data_sources/manager.py` | Explicit legacy/manual import boundaries; current release validation uses `professional_ui` |
| MIGRATED_DELETED | `modules/snapshots/`, `modules/market_data/zapi_sector_metadata.py`, Portfolio Backfill v1 | Tests/workflows moved to current owners; obsolete source removed |
| GENERATED | `data/output/`, `logs/`, `data/state/` | Ignored; never committed |
| FIXTURE | `tests/fixtures/` | Retained and test-visible |
| ARCHIVED_DOCUMENTATION | `docs/archive/v1.1/`, `docs/archive/v1.7/` | Historical evidence only; retained, indexed, and excluded from current operational guidance |
| SECRET_RISK | local `config/telegram.json`, `.env` | Credentials removed; environment-only policy |

The ordered cleanup keeps the compatibility chain explicit, moves historical
documentation under the archive index, and removes only superseded source when
separately approved. Imports, entry points, CI, config references, and backward
compatibility must remain checked during final validation.

