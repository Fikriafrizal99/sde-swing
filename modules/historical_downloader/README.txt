HISTORICAL DOWNLOADER — SDE SWING V1.2
======================================

Sumber live: Yahoo Finance.
Policy produksi: LAST_CLOSED_CANDLE.
Mendukung incremental overlap, full backfill, retries, batch, fallback policy,
dan deterministic evaluation datetime untuk regression test.

Penggunaan resmi melalui RUN_SDE.bat.
Standalone:
python modules\historical_downloader\historical_downloader.py ^
  modules\historical_downloader\Stockbit_Watchlist_2026-07-19_normalized.csv ^
  --output data\output\historical --period 2y
