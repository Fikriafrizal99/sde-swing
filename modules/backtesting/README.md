# Swing Analytics / Backtesting — SDE Swing V1.2

Engine menghitung outcome forward, MFE/MAE, market-regime summary, repeated
signal suppression, dan watchlist outcomes untuk horizon 1, 3, 5, 7, 10, 20
hari bursa.

Penggunaan resmi melalui master pipeline. Standalone:

```bash
python modules/backtesting/backtest_engine.py \
  data/output/decision/FINAL_DECISION_V3.csv \
  data/output/historical/by_symbol \
  data/input/IHSG.csv \
  --output-dir data/output/analytics/manual
```

`archive_daily_signal.py` dihapus karena fungsi archive dan lineage sudah
ditangani database archiver serta run manifest.
