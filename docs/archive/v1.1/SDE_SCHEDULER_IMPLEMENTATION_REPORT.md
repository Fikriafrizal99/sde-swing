# SDE Swing Scheduler Implementation Report

Tanggal: 2026-07-24

## Ringkasan

Implementasi menambahkan job runner resmi untuk SDE Swing tanpa mengganti core scoring, provider, broker fusion, decision engine, exit engine, analytics, database archive, atau formatter Telegram lama.

Manual `RUN_SDE.bat` sekarang masuk lewat:

```text
RUN_SDE.bat
  -> run_sde_job.py --job full_manual
  -> master_pipeline.py --refresh-data --interactive-yahoo --no-telegram
  -> preview
  -> delivery idempotent
```

## Job Baru

- `market_outlook`
- `post_market`
- `final_watchlist`
- `full_manual`

Argument yang tersedia:

- `--dry-run`
- `--no-telegram`
- `--force`
- `--trade-date YYYY-MM-DD`
- `--debug`

## Safety Layer

- Lock per job di `data/state/scheduler/locks`.
- Stale lock cleanup berbasis umur lock dan PID.
- Status job JSON di `data/output/job_status`.
- Preview report di `data/output/previews/<trade-date>`.
- Telegram idempotency di `data/state/scheduler/telegram_idempotency.json`.
- Delivery log JSONL di `data/state/scheduler/delivery_log.jsonl`.
- Failed payload storage di `data/output/failed_delivery`.
- Kalender trading terpusat di `config/trading_calendar.json`.
- Semua waktu job runner memakai `Asia/Jakarta`.

## File Dibuat

- `run_sde_job.py`
- `modules/job_runner/__init__.py`
- `modules/job_runner/runtime.py`
- `modules/job_runner/core.py`
- `modules/job_runner/reports.py`
- `modules/job_runner/delivery.py`
- `config/scheduler.json`
- `config/trading_calendar.json`
- `RUN_SDE_MARKET_OUTLOOK.bat`
- `RUN_SDE_POST_MARKET.bat`
- `RUN_SDE_FINAL_WATCHLIST.bat`
- `tests/test_sde_job_runner.py`
- `docs/SDE_JOB_SCHEDULER_AUDIT.md`
- `docs/SDE_SCHEDULER_IMPLEMENTATION_REPORT.md`
- `docs/WINDOWS_TASK_SCHEDULER_GUIDE.md`
- `scheduler/windows/SDE_MARKET_OUTLOOK.xml`
- `scheduler/windows/SDE_POST_MARKET.xml`
- `scheduler/windows/SDE_FINAL_WATCHLIST.xml`

## File Diubah

- `RUN_SDE.bat`

## Dry-Run yang Dilakukan

```text
run_sde_job.py --job market_outlook --dry-run --trade-date 2026-07-24
run_sde_job.py --job post_market --dry-run --trade-date 2026-07-24
run_sde_job.py --job final_watchlist --dry-run --trade-date 2026-07-24
```

Hasil:

- Market Outlook preview tersimpan.
- Post Market preview tersimpan: Closing Bell dan Daily Signal Recap.
- Final Watchlist preview tersimpan: Final Watchlist, Detail Sinyal, dan Evaluasi.
- Telegram tidak terkirim karena mode dry-run.

## Testing

```text
python -m unittest tests.test_sde_job_runner -v
python -m unittest discover -s tests -p "test*.py" -v
```

Hasil:

- New job-runner tests: 5/5 PASS.
- Full regression suite: 37/37 PASS.

## Risiko Tersisa

- Kalender libur Bursa harus diisi dan dipelihara di `config/trading_calendar.json`.
- Live Telegram tidak diuji karena token/chat ID kosong; gunakan dry-run dan satu laporan uji sebelum mass-send.
- V1.4 menambahkan provider Global Market Yahoo-only; jika Yahoo/dependency gagal, instrumen global tampil `DATA_NOT_AVAILABLE` tanpa angka dummy.
- Windows Task Scheduler XML memakai placeholder `__PROJECT_DIR__` dan perlu diganti sebelum import.
