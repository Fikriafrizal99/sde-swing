SDE SWING V1.6.1 - HOTFIX BROKER NAVIGATOR + MANUAL BROKER BREAK
================================================================

Patch ini khusus untuk folder SDE Swing V1.6.1 yang sudah terpasang.
Patch tidak membawa config, token Telegram, data saham, Broker Summary, atau file output.

CARA PASANG
-----------
1. Tutup semua CMD SDE yang masih berjalan.
2. Extract ZIP patch ini.
3. Copy seluruh isi hasil extract ke ROOT folder SDE V1.6.1 kamu.
4. Pilih Replace/Overwrite untuk file yang sama.

ALUR SETELAH PATCH
------------------
1. Jalankan RUN_POST_MARKET.bat -> mode 1.
2. Post Market otomatis membuat ulang:
   data\output\candidates\BROKER_NAVIGATOR_SYMBOLS.csv
3. Jalankan RUN_FINAL_WATCHLIST.bat -> mode 1.
4. Jika Broker Summary tanggal hari ini belum tersedia, CMD masuk BROKER BREAK.
5. Gunakan BROKER_NAVIGATOR_SYMBOLS.csv pada Tampermonkey Broker Navigator.
6. Setelah file berikut masuk ke Downloads, proses lanjut otomatis:
   - BROKER_SUMMARY_COMBINED_YYYY-MM-DD.csv
   - BROKER_RAW_COMBINED_YYYY-MM-DD.csv (untuk rincian broker; optional untuk scoring)

CATATAN
-------
- Broker Summary lama tidak akan dipakai untuk tanggal teknikal baru.
- Final Watchlist manual tidak lagi langsung berhenti karena cutoff 18:30.
- Task Scheduler tetap non-interaktif dan tetap menggunakan cutoff otomatis.
- Mode Preview Final Watchlist tidak membuka broker break.

FILE YANG DIUBAH
----------------
- RUN_FINAL_WATCHLIST.bat
- run_sde_job.py
- modules\job_runner\core.py
- modules\job_runner\runtime.py
- modules\broker_bridge\wait_for_broker_export.py
