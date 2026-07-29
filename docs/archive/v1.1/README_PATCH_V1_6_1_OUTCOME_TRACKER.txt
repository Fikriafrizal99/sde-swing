SDE SWING V1.6.1 - HOTFIX OUTCOME TRACKER & PERFORMANCE EVALUATION
===================================================================

TUJUAN
------
Patch ini menambahkan pencatatan rekomendasi permanen, evaluasi trigger,
TP/SL/max hold, win rate, dan laporan evaluasi. Scoring teknikal, Broker
Fusion, Decision Engine, format Market Outlook, serta format Final Watchlist
tidak diubah.

CARA PASANG
-----------
1. Tutup semua CMD SDE.
2. Extract ZIP patch.
3. Copy seluruh isi patch ke root SDE_SWING_V1_6_1 Active.
4. Pilih Replace/Overwrite untuk file yang sama.
5. Jalankan RUN_SDE.bat.
6. Pilih [7] Performance & Evaluation.
7. Pilih [1] Update semua outcome dan win rate.

Patch tidak membawa dan tidak menimpa config, token Telegram, database,
historical saham, Broker Summary, atau output pengguna.

ALUR OTOMATIS
-------------
POST MARKET
- Setelah historical diperbarui, outcome rekomendasi lama ikut diperbarui.
- Post Market tetap mengirim format ringkas yang sama.

FINAL WATCHLIST
- BUY CONFIRMED dan BUY CANDIDATE dicatat ke ledger permanen.
- Sinyal yang sama tidak dihitung berulang kali selama masih aktif.
- Setelah dicatat, trigger/entry/TP/SL dievaluasi dari historical terbaru.

FULL MANUAL PIPELINE
- Setelah pipeline selesai, sinyal dan outcome juga disinkronkan.

STATUS OUTCOME
--------------
WAITING_TRIGGER : trigger/area entry belum tercapai.
OPEN            : trigger sudah terjadi dan posisi sedang dievaluasi.
WIN             : TP1/TP2 tercapai atau max hold ditutup positif.
LOSS            : stop loss tercapai atau max hold ditutup negatif.
EXPIRED         : BUY CANDIDATE tidak trigger dalam 7 hari bursa.
CANCELLED       : sinyal turun status sebelum trigger.
AMBIGUOUS       : hasil tidak dapat ditetapkan secara pasti.
INVALID_DATA    : plan, trigger, atau kualitas data tidak layak dievaluasi.

ATURAN WIN RATE
---------------
Win Rate = WIN / (WIN + LOSS)

OPEN, WAITING_TRIGGER, EXPIRED, CANCELLED, AMBIGUOUS, dan INVALID_DATA tidak
dimasukkan ke pembagi win rate resmi. Conservative Win Rate juga tersedia
dengan AMBIGUOUS dimasukkan ke pembagi.

Aturan candle konservatif tetap berlaku: bila TP dan SL tersentuh pada candle
harian yang sama, SL dianggap terjadi lebih dahulu.

OUTPUT
------
data/output/analytics/performance/SIGNAL_OUTCOME_LEDGER.csv
data/output/analytics/performance/PERFORMANCE_SUMMARY.csv
data/output/analytics/performance/PERFORMANCE_BY_SETUP.csv
data/output/analytics/performance/PERFORMANCE_BY_SIGNAL_TYPE.csv
data/output/analytics/performance/PERFORMANCE_BY_BROKER_CONFIDENCE.csv
data/output/analytics/performance/PERFORMANCE_BY_MARKET_REGIME.csv
data/output/analytics/performance/PERFORMANCE_TELEGRAM.txt

Ledger utama juga disimpan permanen di:
data/database/sde_swing_history.db
Table: signal_outcome_ledger

MENU PERFORMANCE
----------------
[1] Update semua outcome dan win rate
[2] Lihat performa keseluruhan
[3] Evaluasi berdasarkan setup
[4] Evaluasi berdasarkan jenis sinyal
[5] Evaluasi berdasarkan broker confidence
[6] Evaluasi berdasarkan market regime
[7] Lihat Signal Outcome Ledger
[8] Kirim laporan performance ke Telegram

CATATAN VALIDASI
----------------
- Laporan performa awal dapat berubah setiap Post Market karena candle baru
  dapat memicu entry, TP, SL, expiry, atau max hold.
- Di bawah 20 closed signals, hasil masih terlalu dini untuk menyimpulkan
  kualitas sistem.
- Patch ini juga dapat mengambil rekomendasi yang saat ini masih tersimpan di
  FINAL_DECISION_V3.csv dan ENTRY_PLANS.csv ketika menu Update dijalankan.
