# SDE Swing V1.7.1 Documentation

This directory contains current operational guidance and canonical contracts.
Historical implementation reports, audit snapshots, and release evidence live
under [`archive/`](archive/README.md) and are not normative runtime guidance.

The official entry point is `run_sde_job.py`. It accepts a job name and creates
one Runtime Context. Legacy scripts remain available for compatibility, but
new work belongs in `modules/runtime/` and `modules/data_sources/`.

Runtime outputs are local and ignored by Git. Credentials are supplied only
through environment variables; `config/telegram.json` contains no credentials.

## Current operational documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — runtime, reporting, compatibility, and persistence boundaries.
- [`CONFIGURATION.md`](CONFIGURATION.md) — active configuration and secret policy.
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — provider ownership, fallback, and provenance.
- [`DATABASE_ARCHIVE.md`](DATABASE_ARCHIVE.md) — historical database archive contract.
- [`RUNTIME_JOBS.md`](RUNTIME_JOBS.md) — scheduled jobs, dependencies, and recovery behavior.
- [`SCHEDULER_RELIABILITY.md`](SCHEDULER_RELIABILITY.md) — retry, locking, and scheduler operations.
- [`TELEGRAM_ROUTING.md`](TELEGRAM_ROUTING.md) — current message routing contract.
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — operator troubleshooting guidance.
- [`MIGRATION_V1_6_TO_V1_7.md`](MIGRATION_V1_6_TO_V1_7.md) — compatibility and migration reference.
- [`LEGACY_FILE_MANIFEST.md`](LEGACY_FILE_MANIFEST.md) — active, compatibility, generated, and archived file classes.

## Canonical contracts

- [`FTJ_BRANDING_CONTRACT.md`](FTJ_BRANDING_CONTRACT.md) — canonical FTJ Community report naming and the hard exclusion that IDX Disclosure / Keterbukaan Informasi remains unchanged.
- [`LIFECYCLE_PRESENTATION_CONTRACT.md`](LIFECYCLE_PRESENTATION_CONTRACT.md) — source of truth for Active Recommendations and Lifecycle Digest presentation.
- [`FINAL_WATCHLIST_ACTION_CONTRACT.md`](FINAL_WATCHLIST_ACTION_CONTRACT.md) — action-line contract: explicit engine trigger first, entry-zone fallback, and S/R never promoted into an implicit trigger.
- [`IDX_DISCLOSURE_WATCHER_ARCHITECTURE.md`](IDX_DISCLOSURE_WATCHER_ARCHITECTURE.md) — isolated IDX disclosure watcher architecture.
- [`IDX_AI_DOCUMENT_READER.md`](IDX_AI_DOCUMENT_READER.md) — isolated AI reader contract for official IDX documents.
- [`WATCHLIST_AI_ARCHITECTURE.md`](WATCHLIST_AI_ARCHITECTURE.md) — isolated Final Watchlist interpretation boundary.

