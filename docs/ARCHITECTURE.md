# Architecture

```text
run_sde_job.py
  -> RunnerContext / RuntimeContext
  -> DataSourceManager
  -> SourceRouter -> canonical records -> quality/conflict checks
  -> snapshot builder -> analysis/decision adapters
  -> report payloads -> TelegramRouter -> unified status writer
```

`DataSourceManager` is the only provider readiness and routing facade. It
returns canonical records plus provenance and health metadata. The decision
engine remains the owner of trading thresholds and protected decision columns.

