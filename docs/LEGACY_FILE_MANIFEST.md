# Legacy file manifest

| Class | Examples | Treatment |
|---|---|---|
| ACTIVE | `run_sde_job.py`, `modules/job_runner/`, `modules/data_sources/` | Official runtime and source layer |
| LEGACY_REQUIRED | `master_pipeline.py`, Stage 1/2 engines | Kept for compatibility and regression tests |
| COMPATIBILITY_ONLY | `modules/telegram/telegram_bot.py`, `modules/telegram/swing_report_builder.py`, `modules/data_sources/manager.py` | Explicit legacy/manual import boundaries; current release validation uses `professional_ui` |
| MIGRATED_DELETED | `modules/snapshots/`, `modules/market_data/zapi_sector_metadata.py`, Portfolio Backfill v1 | Tests/workflows moved to current owners; obsolete source removed |
| GENERATED | `data/output/`, `logs/`, `data/state/` | Ignored; never committed |
| FIXTURE | `tests/fixtures/` | Retained and test-visible |
| ARCHIVE_CANDIDATE | old reports and archived v1.1 docs | No deletion in this change; review separately |
| SECRET_RISK | local `config/telegram.json`, `.env` | Credentials removed; environment-only policy |

The ordered cleanup keeps the compatibility chain explicit, moves the snapshot
and ZAPI sector tests to current owners, and removes only the superseded v1
Portfolio Backfill source. Imports, entry points, CI, config references, and
backward compatibility were checked before final validation.

