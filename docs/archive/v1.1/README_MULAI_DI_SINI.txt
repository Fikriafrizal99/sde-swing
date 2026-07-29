SDE SWING V1.6.1 - MULAI DI SINI
==================================

1. Buka RUN_SDE.bat.
2. Pilih Maintenance > Install requirements untuk instalasi pertama.
3. Isi token dan chat ID di config\telegram.json.
4. Pastikan topic routing di config\scheduler.json sesuai Telegram kamu.
5. Jalankan Test Telegram dari RUN_SDE.bat.
6. Untuk proses harian, pilih Market Outlook, Post Market, atau Final Watchlist dari menu.

FILE BROKER
-----------
Tampermonkey menghasilkan dua file untuk tanggal yang sama:
- BROKER_SUMMARY_COMBINED_YYYY-MM-DD.csv
- BROKER_RAW_COMBINED_YYYY-MM-DD.csv

Biarkan keduanya di Downloads. Final Watchlist akan mengambil summary untuk scoring dan raw untuk menampilkan Top Buyer/Top Seller, nilai, dan average price per broker.

BAT HARIAN DI ROOT
------------------
- RUN_SDE.bat
- RUN_MARKET_OUTLOOK.bat
- RUN_POST_MARKET.bat
- RUN_FINAL_WATCHLIST.bat
- CHECK_SDE_STATUS.bat

TASK SCHEDULER
--------------
Setelah extract atau pindah folder:
RUN_SDE.bat > Maintenance > Generate Task Scheduler XML

Import XML baru dari scheduler\windows\generated.

VALIDASI
--------
RUN_SDE.bat > Maintenance > Verify manifest
RUN_SDE.bat > Maintenance > Release validation
