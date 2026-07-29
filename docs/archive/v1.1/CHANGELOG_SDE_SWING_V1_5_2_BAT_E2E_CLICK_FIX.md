# Changelog - SDE Swing V1.5.2 BAT E2E Click Fix

Pipeline version: `SDE_SWING_V1_5_2_BAT_E2E_CLICK_FIX`

## Masalah yang Diperbaiki

- `RUN_SDE_MARKET_OUTLOOK.bat` dan `RUN_SDE_POST_MARKET.bat` terlihat hanya muncul sebentar saat diklik dari Explorer karena launcher scheduler langsung keluar dan tidak menampilkan status akhir.
- Post Market mode scheduler melakukan refresh Yahoo saham. Jika user hanya ingin memakai data existing yang sudah ada, jalur yang tepat belum tersedia sebagai BAT klik manual.
- Jika laporan sudah terkirim lewat test manual Python, idempotency dapat menahan pengiriman ulang dari BAT scheduler sehingga terlihat seperti tidak bekerja.
- Paket sebelumnya masih bisa membawa status/log/preview/cache runtime dari folder lama, termasuk path absolut lama dan snapshot JSON Yahoo lama.
- CMD/PowerShell bisa menampilkan emoji UTF-8 sebagai teks rusak jika codepage belum UTF-8.

## Perubahan

- Menambahkan launcher cek manual:
  - `RUN_SDE_MARKET_OUTLOOK_CEK.bat`
  - `RUN_SDE_POST_MARKET_CEK.bat`
  - `RUN_SDE_FINAL_WATCHLIST_CEK.bat`
  - `RUN_SDE_CEK_STATUS_SEMUA.bat`
- Menambahkan launcher kirim ulang dengan konfirmasi:
  - `RUN_SDE_MARKET_OUTLOOK_KIRIM_ULANG.bat`
  - `RUN_SDE_POST_MARKET_KIRIM_ULANG.bat`
- Menambahkan `RUN_SDE_RESET_STATUS_RUNTIME.bat` dan `tools/clean_runtime_artifacts.py` untuk membersihkan status/log/cache/preview sementara tanpa menghapus data utama.
- Menambahkan `tools/set_python_cmd.bat` supaya semua BAT memakai Python yang sama, dengan override `SDE_PYTHON` bila diperlukan.
- Menambahkan `GENERATE_TASK_SCHEDULER_XML.bat` dan mengecualikan `scheduler/windows/generated/` dari manifest supaya XML absolut tidak ikut nyangkut dari folder lama.
- Semua BAT utama sekarang mengaktifkan UTF-8 console dengan `chcp 65001`.
- BAT scheduler menerima argumen tambahan lewat `%*`, sehingga bisa dipakai dari CMD untuk override seperti `--trade-date` atau `--force`.
- `tools/print_job_status.py` sekarang menampilkan status Telegram, rincian delivery per report, error, lokasi preview, dan peringatan jika status masih memuat path absolut dari folder lain.
- Manifest generator/verifier mengecualikan artefak runtime volatile agar file status/cache/log tidak ikut dianggap bagian inti rilis.

## Cara Pakai Singkat

- Scheduler otomatis:
  - `RUN_SDE_MARKET_OUTLOOK.bat`
  - `RUN_SDE_POST_MARKET.bat`
  - `RUN_SDE_FINAL_WATCHLIST.bat`
- Klik manual cek tanpa kirim Telegram:
  - `RUN_SDE_MARKET_OUTLOOK_CEK.bat`
  - `RUN_SDE_POST_MARKET_CEK.bat`
  - `RUN_SDE_FINAL_WATCHLIST_CEK.bat`
- Sengaja kirim ulang setelah test manual Python:
  - `RUN_SDE_MARKET_OUTLOOK_KIRIM_ULANG.bat`
  - `RUN_SDE_POST_MARKET_KIRIM_ULANG.bat`
