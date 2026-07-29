SDE SWING V1.6.2 - HOTFIX ADAPTIVE FINAL WATCHLIST (NO BUY)
===========================================================

TUJUAN
------
Memperbaiki Final Watchlist ketika tidak ada BUY CONFIRMED atau BUY CANDIDATE.
WATCH HIGH tidak lagi tampil sebagai daftar singkat tanpa konteks.

PERUBAHAN
---------
1. Saat hanya ada WATCH HIGH, Final Watchlist menampilkan:
   - pemberitahuan BELUM ADA SINYAL BUY;
   - setup;
   - trigger atau area;
   - broker state, confidence, dan net flow bila tersedia;
   - kendala utama;
   - status eksekusi yang tidak menyesatkan;
   - arahan bahwa belum ada prioritas transaksi.

2. Kalimat "Rencana entry valid" tidak lagi ditampilkan sendirian pada WATCH HIGH.
   Diganti menjadi:
   "Plan teknikal valid, tetapi keputusan BUY belum terkonfirmasi."

3. Jika BUY CONFIRMED atau BUY CANDIDATE tersedia, format compact sebelumnya tetap dipakai.

TIDAK DIUBAH
------------
- Technical Score
- Broker Score dan Broker Fusion
- Decision Engine
- Entry/TP/SL calculation
- Market Outlook
- Post Market
- Outcome Tracker
- Task Scheduler

FILE YANG DIUBAH
----------------
modules/telegram/professional_ui.py

CARA PASANG
-----------
1. Extract ZIP ini.
2. Copy seluruh isi ke root SDE_SWING_V1_6_2_COMPLETE_BASELINE aktif.
3. Pilih Replace/Overwrite.
4. Jalankan RUN_FINAL_WATCHLIST.bat dengan mode Preview atau Existing Data.

Patch ini aman dipasang setelah hotfix Task Scheduler Encoding V2 karena file yang diubah berbeda.
