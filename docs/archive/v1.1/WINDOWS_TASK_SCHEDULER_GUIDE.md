# Windows Task Scheduler Guide

Semua jadwal memakai WIB (`Asia/Jakarta`) dan tetap divalidasi ulang oleh aplikasi melalui `config/trading_calendar.json`.

## Command

Jalankan dari root project:

```bat
python run_sde_job.py --job market_outlook
python run_sde_job.py --job post_market
python run_sde_job.py --job final_watchlist
python run_sde_job.py --job full_manual
```

Mode aman:

```bat
python run_sde_job.py --job market_outlook --dry-run
python run_sde_job.py --job post_market --dry-run
python run_sde_job.py --job final_watchlist --no-telegram
python run_sde_job.py --job full_manual --force
```

## BAT

- `RUN_SDE_MARKET_OUTLOOK.bat`
- `RUN_SDE_POST_MARKET.bat`
- `RUN_SDE_FINAL_WATCHLIST.bat`
- `RUN_SDE.bat` untuk fallback manual `full_manual`

## Jadwal

- 07:30 WIB: Market Outlook
- 16:30 WIB: Post-Market Orchestrator
- 18:00 WIB: Final Watchlist

## Membuat Task Manual

1. Buka Task Scheduler.
2. Pilih `Create Task`.
3. Tab `General`: centang `Run whether user is logged on or not`.
4. Tab `Triggers`: buat trigger harian sesuai jam WIB.
5. Tab `Actions`: pilih `Start a program`.
6. Program: path BAT job, misalnya `C:\path\to\SDE\RUN_SDE_MARKET_OUTLOOK.bat`.
7. `Start in`: root project SDE Swing.
8. Tab `Settings`: centang `Do not start a new instance`.
9. Atur stop limit:
   - Market Outlook: 30 menit
   - Post Market: 60 menit
   - Final Watchlist: 90 menit
10. Test dulu dengan `--dry-run` atau `--no-telegram`.

## Import XML

Folder `scheduler/windows/` berisi XML template. Ganti placeholder `__PROJECT_DIR__` dengan root project absolut sebelum import.

## Menonaktifkan Scheduler

Disable task dari Task Scheduler UI. Jangan hapus file BAT atau source code; tombol manual tetap dapat dipakai sebagai fallback.

