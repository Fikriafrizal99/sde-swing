# SDE Swing V1.6.1 — Telegram & BAT Audit

## Tujuan

Merapikan penggunaan harian tanpa mengubah Market Outlook atau menurunkan kualitas sinyal V1.6.0.

## Temuan Sebelum Perbaikan

1. Post Market mengirim beberapa laporan dengan informasi teknikal yang saling mengulang.
2. Final Watchlist terlalu panjang, tetapi belum menunjukkan nilai dan average price broker individual.
3. BUY CANDIDATE belum mendapat detail broker yang cukup untuk early screening.
4. Root membawa terlalu banyak BAT untuk normal, cek, preview, resend, reset, dan scheduler.
5. XML lama dapat menunjuk launcher/path versi sebelumnya.

## Implementasi

### Post Market

Satu payload `post_market` menggantikan kombinasi Closing Bell dan Daily Recap. Laporan dibatasi pada keadaan pasar, statistik proses, dan kandidat teknikal awal.

### Final Watchlist

Ringkasan memakai tiga tier: BUY CONFIRMED, BUY CANDIDATE, WATCH HIGH. Detail broker dibuat untuk BUY CONFIRMED dan BUY CANDIDATE agar momentum dapat dipantau sebelum trigger final.

Broker detail dibaca dari `BROKER_RAW_COMBINED_<trade-date>.csv`, bukan ditebak dari summary. Data yang digunakan:

- SIDE;
- RANK;
- BROKER_CODE;
- NET_VALUE;
- AVG_PRICE;
- FREQUENCY/GROSS_VALUE bila tersedia.

Weighted average dihitung dari average price yang dibobot nilai absolut transaksi broker pada sisi yang sama.

### BAT

Root menjadi lima launcher harian. Task Scheduler dan maintenance dipisahkan agar file yang diklik pengguna tidak bercampur dengan file internal.

## Failure Handling

- Raw tanggal tidak cocok: ditolak.
- Raw tidak ditemukan: Final Watchlist tetap berjalan dengan warning dan fallback nama broker.
- Telegram gagal: status delivery tetap menghasilkan exit code gagal sesuai fix V1.5.3.
- Menu BAT invalid: kembali ke menu, bukan menjalankan job lain.
- Task Scheduler: memakai launcher non-interaktif.

## Validasi yang Dijalankan

- Unit/regression tests seluruh pipeline.
- Test load dan auto-copy raw dari Downloads.
- Test dry-run tidak menyalin raw.
- Test jumlah BAT root tepat lima.
- Test menu BAT tidak memakai unconditional `& goto`.
- Test XML generator mengarah ke launcher `scheduler/SCHEDULE_*.bat`.
- Python compile untuk seluruh source.
- Manifest dan verifikasi ZIP setelah ekstraksi.

## Batasan

Rincian broker adalah snapshot satu sesi. Analisis konsistensi broker 3 hari/5 hari belum diaktifkan. Broker Raw tidak mengubah Broker Score pada rilis ini; fungsinya adalah screening dan transparansi Telegram.

## Market Outlook Regression Boundary

`format_market_outlook` dan `market_outlook_payload` dibandingkan langsung dengan paket V1.6.0. Keduanya identik; tidak ada perubahan pada format atau alur payload Market Outlook.

## Hasil Test Final

- 86 unit/regression tests: PASS.
- 5 test khusus V1.6.1 mencakup raw auto-copy, dry-run, BAT root, menu control panel, dan scheduler launcher.
- Python compile: PASS tanpa syntax warning.
