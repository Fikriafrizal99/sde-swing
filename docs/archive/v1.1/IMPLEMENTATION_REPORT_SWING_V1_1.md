# Implementation Report Swing V1.1

## Ringkasan

Implementasi V1.1 menambahkan data freshness, manifest per-run, broker date control, SQLite archive, analytics outcome, dan Telegram Swing reporting. Formula scoring dan decision tidak diubah.

Status implementasi:

```text
IMPLEMENTATION COMPLETE
OFFLINE VALIDATION PASSED
LIVE OPERATIONAL VERIFICATION REQUIRED
```

## Catatan Pengujian Live

Live Yahoo Finance refresh tidak dijalankan pada environment pengembangan karena memerlukan koneksi network aktif dan respons aktual dari provider.

Live Stockbit/Tampermonkey end-to-end juga tidak dijalankan karena membutuhkan browser, akun Stockbit, Tampermonkey, export manual, serta file broker aktual.

Seluruh skenario terkait telah diuji menggunakan validation layer, simulation test, dan offline test fixture yang tersedia di dalam paket.

Hasil offline fixture tidak dianggap sebagai bukti bahwa provider live pasti berhasil. Pengujian live tetap perlu dilakukan oleh user pada environment operasional.

Paket ini tidak diklaim sebagai `PRODUCTION LIVE VERIFIED`.

## Modul Baru

- `swing_utils.py`: Run ID, hash, JSON/CSV helper, trading date helper, normalisasi simbol.
- `modules/database/swing_history_db.py`: SQLite archive dan dedup canonical prices.
- `modules/telegram/swing_report_builder.py`: builder tunggal untuk Telegram message, preview dry-run, dan TXT report.
- `tests/test_swing_v1_1.py`: regression fixture offline.

## Modul Diubah

- `master_pipeline.py`: satu Run ID global, central run manifest, broker fusion path, analytics, DB archive, Telegram swing dry-run.
- `historical_downloader.py`: closed candle validation, status per simbol, Yahoo manifest.
- `historical_downloader.py`: incremental Yahoo refresh, skip already-current tanpa network, batch download terbatas, dan fallback batch ke individual.
- `technical_feature_engine.py`: pre-validation dan technical manifest.
- `technical_candidate_selector.py`: candidate manifest dan hash hasil.
- `broker_bridge/wait_for_broker_export.py`: broker date policy `exact/latest/manual/ask`.
- `broker_bridge/broker_navigator_export.py`: navigator manifest.
- `broker_fusion.py`, `decision_engine.py`, `decision_engine_v3_1.py`, `exit_engine.py`: metadata Run ID dan manifest tanpa mengubah formula.
- `backtesting/backtest_engine.py`: horizon D1/D3/D7 dan `WATCHLIST_OUTCOMES.csv`.
- `telegram/telegram_bot.py`: command baru `swing`.

## Output Baru

- `data/output/manifests/SWING_RUN_MANIFEST_<RUN_ID>.json`
- `data/output/manifests/YAHOO_REFRESH_MANIFEST_<RUN_ID>.json`
- `data/output/manifests/YAHOO_SYMBOL_STATUS_<RUN_ID>.csv`
- `data/output/manifests/CANDIDATE_MANIFEST_<RUN_ID>.json`
- `data/database/sde_swing_history.db`
- `data/output/reports/<RUN_ID>/*.txt`
- `data/output/telegram_preview/<RUN_ID>/*.txt`
- `LIVE_TEST_GUIDE.md`
