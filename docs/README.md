# SDE Swing V1.7.0 Multi-Source

The official entry point is `run_sde_job.py`. It accepts a job name and creates
one Runtime Context. Legacy scripts remain available for compatibility, but
new work belongs in `modules/runtime/` and `modules/data_sources/`.

Runtime outputs are local and ignored by Git. Credentials are supplied only
through environment variables; `config/telegram.json` contains no credentials.

