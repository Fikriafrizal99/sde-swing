# SDE Swing V1.7.1

Multi-Source Decision Support System

The official entry point is `run_sde_job.py`. It accepts a job name and creates
one Runtime Context. Legacy scripts remain available for compatibility, but
new work belongs in `modules/runtime/` and `modules/data_sources/`.

Runtime outputs are local and ignored by Git. Credentials are supplied only
through environment variables; `config/telegram.json` contains no credentials.

## Canonical contracts

- `LIFECYCLE_PRESENTATION_CONTRACT.md` — single source of truth for Active Recommendations and Lifecycle Digest Telegram presentation, including AGE vs Last Scan rules and automatic Final Watchlist delivery behavior.
- `IDX_DISCLOSURE_WATCHER_ARCHITECTURE.md` — isolated IDX disclosure watcher architecture.
- `IDX_AI_DOCUMENT_READER.md` — isolated AI document-reader contract for official IDX documents.

