# Changelog - SDE Swing V1.5.3 Report Delivery Recovery Fix

Pipeline version: `SDE_SWING_V1_5_3_REPORT_DELIVERY_RECOVERY_FIX`

## Masalah yang Ditemukan

1. Kegagalan pengiriman Telegram menghasilkan status `SUCCESS_WITH_DELIVERY_FAILURE`, tetapi exit code tetap `0`. Akibatnya BAT menampilkan seolah-olah job selesai dan jendela CMD langsung tertutup.
2. Idempotency yang menahan laporan duplikat juga tetap dianggap sukses, sehingga pengguna tidak tahu bahwa tidak ada pesan baru yang dikirim.
3. Refresh Global Market yang gagal dapat menulis snapshot coverage rendah ke file snapshot utama, walaupun snapshot valid pada tanggal yang sama sudah tersedia.
4. Post Market berhenti total ketika refresh Yahoo saham gagal, walaupun snapshot teknikal existing untuk trade date yang sama tersedia dan dapat dipakai.
5. BAT utama tidak otomatis menampilkan isi status job setelah proses selesai.

## Perbaikan

- Kegagalan Telegram kini menghasilkan status `DELIVERY_FAILED` dan exit code `50`.
- Semua laporan yang tertahan idempotency menghasilkan status `DUPLICATE_SUPPRESSED` dan exit code `30`.
- Market Outlook menolak pengiriman jika coverage global market di bawah guardrail dan tidak ada snapshot valid untuk fallback.
- Snapshot Global Market valid tidak lagi ditimpa hasil refresh gagal. Hasil refresh gagal disimpan terpisah untuk audit.
- Market Outlook otomatis memakai snapshot valid pada tanggal yang sama bila refresh Yahoo gagal memenuhi minimum coverage.
- Post Market otomatis fallback ke snapshot teknikal existing pada tanggal yang sama bila refresh Yahoo gagal.
- Mode `--preview-existing` Post Market sekarang benar-benar memuat dan memvalidasi snapshot existing.
- Market Outlook dan Post Market menampilkan progres empat tahap di CMD.
- BAT utama otomatis menampilkan status akhir, delivery per laporan, dan error Telegram. Pada kegagalan, jendela bertahan 20 detik agar hasil dapat dibaca.

## Exit Code Penting

```text
0  = sukses atau dry-run/no-telegram sesuai perintah
1  = data/pipeline invalid atau exception
30 = laporan tidak dikirim karena duplicate suppression
50 = pengiriman Telegram gagal
```

## Validasi

- Python compile: PASS.
- Unit/regression tests: 69 PASS.
- Delivery failure Market Outlook: tervalidasi menghasilkan exit code 50.
- Delivery failure Post Market: tervalidasi menghasilkan exit code 50.
- Global Market fallback tanpa overwrite snapshot valid: PASS.
- Live Telegram API tidak dijalankan pada paket audit karena token/chat ID tidak disertakan di ZIP.
