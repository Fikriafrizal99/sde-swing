# Data Lineage Swing

Satu `Run_ID` dibuat oleh `master_pipeline.py` dan diteruskan ke:

- Yahoo Downloader
- Technical Feature Engine
- Candidate Selector
- Broker Navigator Export
- Broker Export Waiter
- Broker Fusion
- Decision Engine
- Exit Engine
- Analytics
- Database Archive
- Telegram Swing Report

## Central Manifest

```text
data/output/manifests/SWING_RUN_MANIFEST_<RUN_ID>.json
```

Berisi status pipeline, data quality, fallback, broker override, source files, output files, warnings, dan errors.

## Dataset Hash

CSV/JSON penting diberi SHA-256 hash. Hash ini masuk ke manifest dan `run_data_snapshots`.

## No Silent Fallback

Jika data tidak fresh:

- pipeline berhenti pada policy `STOP`;
- atau lanjut dengan `STALE_ACCEPTED` jika user/config menyetujui;
- warning masuk ke console, manifest, database, dan Telegram warning.

