# Legacy file manifest

| Class | Examples | Treatment |
|---|---|---|
| ACTIVE | `run_sde_job.py`, `modules/job_runner/`, `modules/data_sources/` | Official runtime and source layer |
| LEGACY_REQUIRED | `master_pipeline.py`, Stage 1/2 engines | Kept for compatibility and regression tests |
| GENERATED | `data/output/`, `logs/`, `data/state/` | Ignored; never committed |
| FIXTURE | `tests/fixtures/` | Retained and test-visible |
| ARCHIVE_CANDIDATE | old reports and archived v1.1 docs | No deletion in this change; review separately |
| SECRET_RISK | local `config/telegram.json`, `.env` | Credentials removed; environment-only policy |

No file was deleted or moved solely to make tests pass. Imports, entry points,
CI, config references, and backward compatibility were checked before adding
the shared runtime facade.

