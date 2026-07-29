# SDE Swing Scheduler Implementation Audit

Tanggal audit: 2026-07-24

## Entry Point Aktual

Manual launcher yang sepadan dengan tombol `RUN SDE REFRESH DATA FINAL` adalah `RUN_SDE.bat`.

Alur sebelum refactor:

```text
RUN_SDE.bat
  -> cd /d "%~dp0"
  -> python master_pipeline.py --refresh-data --interactive-yahoo
```

`RUN_SDE_EXISTING_DATA.bat` menjalankan `python master_pipeline.py` tanpa refresh data.

## Mapping Pipeline Aktual

`master_pipeline.py` adalah jalur produksi resmi. Urutan aktualnya:

```text
master_pipeline.py
  -> load config/pipeline.json
  -> generate Run_ID
  -> write SWING_RUN_MANIFEST_<Run_ID>.json
  -> historical_downloader.py
  -> update_ihsg.py
  -> technical_feature_engine.py
  -> technical_candidate_selector.py
  -> broker_navigator_export.py
  -> wait_for_broker_export.py
  -> broker_fusion.py
  -> decision_engine.py
  -> exit_engine.py
  -> backtest_engine.py
  -> swing_history_db.py
  -> telegram_bot.py swing
```

## Dependency Mapping

```text
Yahoo / historical OHLCV
  -> Technical Feature Engine
  -> Technical Candidate Selector
  -> Broker Navigator Symbols
  -> Stockbit Broker Summary Export
  -> Broker Fusion
  -> Decision Engine
  -> Exit Engine
  -> Analytics and Database Archive
  -> Telegram Report Builder / Sender
```

## Provider dan Input

- Historical market data: `modules/historical_downloader/historical_downloader.py` memakai Yahoo/live atau fixture eksplisit.
- IHSG: `modules/market_data/update_ihsg.py`.
- Broker summary: `modules/broker_bridge/wait_for_broker_export.py` membaca export Stockbit/Tampermonkey dari folder Downloads atau file manual.
- Technical engine: `modules/technical_feature_engine/technical_feature_engine.py`.
- Broker fusion: `modules/broker_fusion/broker_fusion.py`.
- Decision engine: `modules/decision_engine/decision_engine.py`.
- Exit engine: `modules/exit_engine/exit_engine.py`.
- Telegram: `modules/telegram/swing_report_builder.py` dan `modules/telegram/telegram_bot.py`.

## Output Utama

- `data/output/historical/by_symbol/*.csv`
- `data/output/technical/latest_technical_features.csv`
- `data/output/candidates/technical_candidates_top30.csv`
- `data/input/broker/BROKER_SUMMARY_LATEST.csv`
- `data/input/FINAL_DECISION_V2.csv`
- `data/output/decision/FINAL_DECISION_V3.csv`
- `data/output/exit/*.csv`
- `data/output/analytics/<Run_ID>/*.csv`
- `data/output/reports/<Run_ID>/*.txt`
- `data/output/telegram_preview/<Run_ID>/*.txt`
- `data/output/manifests/*.json`

## Risiko yang Ditemukan

- Manual BAT langsung memanggil `master_pipeline.py`, sehingga belum ada lock job-level sebelum refactor ini.
- Telegram dikirim di akhir `master_pipeline.py`; jika Telegram gagal, retry aman membutuhkan pemisahan delivery dari analysis.
- Scheduler lama belum ada, sehingga belum ada idempotency per report.
- Kalender bursa belum terpusat selain daftar holiday pada config data freshness.
- Post-market dan final watchlist belum dipisahkan menjadi job sendiri.
- Topic routing Telegram belum tersedia di config lama.

## Perubahan yang Dibuat

- Menambahkan `run_sde_job.py` sebagai CLI runner job.
- Menambahkan package `modules/job_runner/` untuk runtime safety, delivery, report preview, dan pemanggilan core engine.
- Mengubah `RUN_SDE.bat` agar memanggil `run_sde_job.py --job full_manual`.
- Menambahkan job wajib:
  - `market_outlook`
  - `post_market`
  - `final_watchlist`
  - `full_manual`
- Menambahkan lock file, stale lock cleanup, job status JSON, preview folder, delivery idempotency, failed payload storage, dan dry-run/no-telegram behavior.
- Menambahkan `config/scheduler.json` dan `config/trading_calendar.json`.
- Menambahkan BAT scheduler: `RUN_SDE_MARKET_OUTLOOK.bat`, `RUN_SDE_POST_MARKET.bat`, `RUN_SDE_FINAL_WATCHLIST.bat`.

## Catatan Batasan

- Formula scoring, decision rules, broker fusion, technical engine, dan formatter Telegram lama tidak diubah.
- Kalender libur bursa memakai config lokal yang harus diisi/diperbarui oleh operator.
- Live Telegram mass-send tidak diuji karena credential tidak tersedia dan dry-run wajib dilakukan dulu.
- V1.4 menambahkan provider Global Market Yahoo-only untuk Market Outlook; instrumen gagal tetap tampil `DATA_NOT_AVAILABLE`.
