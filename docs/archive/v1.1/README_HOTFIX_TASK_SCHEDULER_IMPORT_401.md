# SDE Swing V1.6.2 - Task Scheduler Import 401 Hotfix

## Masalah

XML hasil generator sebelumnya tidak sesuai urutan schema Windows Task Scheduler:

- `Enabled` diletakkan setelah `ScheduleByDay` di dalam `CalendarTrigger`.
- `Actions Context="Author"` dipakai tanpa deklarasi `Principal id="Author"`.

Akibatnya Windows dapat menolak import dengan pesan XML incorrectly formatted/out of range, sering ditampilkan sebagai posisi seperti `(2,401)`.

## Perbaikan

- Urutan trigger menjadi `Enabled -> StartBoundary -> ScheduleByDay`.
- `Actions Context` yang tidak valid dihapus.
- Task XML memakai schema version `1.3` untuk kompatibilitas Windows 10/11.
- BAT dijalankan melalui `cmd.exe /d /c` agar path dengan spasi aman.
- XML dibuat dalam format multi-line agar lokasi error mudah dibaca.
- Generator melakukan validasi struktur sebelum menyatakan sukses.

## Cara Pakai

1. Copy patch ke root project dan pilih Replace/Overwrite.
2. Hapus folder XML lama:
   `scheduler\windows\generated`
3. Jalankan:
   `maintenance\GENERATE_SCHEDULER_XML.bat`
4. Import tiga XML baru dari:
   `scheduler\windows\generated`
5. Pada tab General pilih akun Windows yang digunakan dan centang `Run with highest privileges` bila diperlukan.
6. Jalankan setiap task melalui tombol `Run` untuk pengujian.

Patch tidak mengubah pipeline, scoring, laporan Telegram, konfigurasi, database, atau data historis.
