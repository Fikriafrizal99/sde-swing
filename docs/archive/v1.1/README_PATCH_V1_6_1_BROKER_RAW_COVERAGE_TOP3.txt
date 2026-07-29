SDE SWING V1.6.1 - HOTFIX BROKER RAW COVERAGE & TOP 3
=====================================================

TUJUAN
------
Patch ini hanya memperbaiki pembacaan Broker Raw dan tampilan broker di Telegram.
Patch TIDAK mengubah Technical Score, Broker Score, Broker Fusion, Decision Engine,
Entry Plan, threshold, atau status BUY/WATCH.

PERUBAHAN
---------
1. Final Watchlist menampilkan Top 3 Buyer dan Top 3 Seller untuk BUY CONFIRMED
   dan BUY CANDIDATE, lengkap dengan nilai transaksi dan average price.
2. Detail kandidat menampilkan coverage:
   - broker yang cocok dengan Broker Summary;
   - nilai transaksi yang tersedia;
   - average price yang tersedia.
3. Average price kosong dipulihkan dari:
   GROSS_VALUE / (GROSS_LOT x 100), lalu fallback NET_VALUE / (NET_LOT x 100).
4. Mendukung variasi nama kolom, simbol .JK, dan label BUY/SELL seperti
   BUYER, SELLER, BELI, dan JUAL.
5. Capture Broker Raw duplikat tidak dijumlah ulang. Sistem memilih baris paling
   lengkap agar nilai tidak terhitung ganda.
6. Top 3 dari Broker Summary tetap ditampilkan meskipun Broker Raw hanya cocok
   sebagian. Data yang belum ditemukan tetap ditulis "belum tersedia".
7. Tampermonkey Navigator dinaikkan ke v3.1.1 agar average price juga dihitung
   langsung di browser bila field average dari Stockbit kosong.

CARA PASANG PATCH
-----------------
1. Tutup CMD SDE.
2. Extract ZIP patch.
3. Copy seluruh isi hasil extract ke root SDE_SWING_V1_6_1 Active.
4. Pilih Replace/Overwrite.
5. Config, token Telegram, historical, output, dan database tidak dibawa patch
   sehingga tidak akan ditimpa.

PENTING - UPDATE TAMPERMONKEY
-----------------------------
Menyalin file ke root tidak otomatis memperbarui script yang sudah terpasang di
browser. Buka Tampermonkey Dashboard, edit Stockbit Broker Summary Auto Navigator,
lalu ganti seluruh isinya menggunakan file:

tampermonkey\Stockbit_Broker_Summary_Auto_Navigator_v3.1.user.js

Pastikan metadata menunjukkan @version 3.1.1, lalu Save.

Existing BROKER_RAW lama masih dapat dibaca oleh Python apabila kolom value dan
lot tersedia. Namun untuk export berikutnya, gunakan userscript v3.1.1.

ALUR TEST
---------
1. Jalankan RUN_POST_MARKET.bat mode Normal.
2. Import BROKER_NAVIGATOR_SYMBOLS.csv ke Broker Navigator.
3. Pastikan dua file terunduh:
   BROKER_SUMMARY_COMBINED_YYYY-MM-DD.csv
   BROKER_RAW_COMBINED_YYYY-MM-DD.csv
4. Jalankan RUN_FINAL_WATCHLIST.bat mode Normal atau Preview.
5. Cek bagian Top Buy, Top Sell, dan Raw coverage.

CONTOH OUTPUT
-------------
Top Buy : DX Rp4,12 miliar @Rp9.075; YP Rp2,31 miliar @Rp9.110; AK Rp1,46 miliar @Rp9.040
Top Sell: CC Rp2,84 miliar @Rp9.180; ZP Rp1,72 miliar @Rp9.155; PD Rp980 juta @Rp9.200
Raw     : 6/6 broker | nilai 6/6 | avg 6/6

VALIDASI
--------
- Python compile: PASS
- 91 unit/regression tests: PASS
- Technical Engine, Broker Fusion, Decision Engine, dan Exit Engine: hash identik
  dengan baseline V1.6.1 + patch sebelumnya.
