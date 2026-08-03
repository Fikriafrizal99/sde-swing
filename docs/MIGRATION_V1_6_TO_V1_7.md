# Migration V1.6 to V1.7

Existing `run_sde_job.py`, `master_pipeline.py`, Stage 1/2 engines, fixtures,
and tests remain import-compatible. New integrations can construct a
`RuntimeContext` or convert a legacy `RunnerContext` with
`RuntimeContext.from_runner_context()`.

Move credentials to environment variables, point source ownership at
`config/data_sources.json`, and use the integrated job names. Runtime outputs
are now grouped under `data/output/{job_status,snapshots,watchlists,decisions,
previews,reports,audits}` and are not committed.

