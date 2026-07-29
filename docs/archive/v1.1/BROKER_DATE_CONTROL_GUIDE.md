# Broker Date and Coverage Control Guide

Broker bridge mendukung policy:

```text
exact
latest
manual
ask
```

`RUN_SDE.bat` menggunakan policy konfigurasi `ask`. Scheduler menggunakan
`exact` agar tidak menunggu input user.

```bat
python master_pipeline.py --refresh-data --scheduler
```

Selain tanggal, Broker Fusion memvalidasi coverage kandidat. Default minimum:

```json
"min_coverage": 0.80,
"allow_partial_broker": false
```

Coverage di bawah minimum menghentikan pipeline. Override hanya boleh dilakukan
secara eksplisit melalui konfigurasi/CLI dan akan masuk ke data quality serta
manifest.

Contoh manual file:

```bat
python modules\broker_bridge\wait_for_broker_export.py ^
  --symbols data\output\candidates\broker_symbols.csv ^
  --downloads "%USERPROFILE%\Downloads" ^
  --output data\input\broker\BROKER_SUMMARY_LATEST.csv ^
  --broker-date-policy manual ^
  --manual-broker-file "%USERPROFILE%\Downloads\BROKER_SUMMARY_COMBINED.csv" ^
  --expected-broker-date 2026-07-23
```
