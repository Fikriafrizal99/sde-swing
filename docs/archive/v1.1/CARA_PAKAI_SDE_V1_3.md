# Cara Pakai SDE Swing V1.3 Scheduler Hardened

Ini versi ringkas untuk mulai memakai paket.

## 1. Install Dependency

Buka folder project, lalu jalankan:

```bat
install_requirements.bat
```

Atau manual:

```bat
pip install -r requirements.txt
```

Dependency penting:

```text
pandas
numpy
yfinance
requests
```

## 2. Test Aman Tanpa Kirim Telegram

Jalankan:

```bat
python run_sde_job.py --job market_outlook --dry-run
python run_sde_job.py --job post_market --preview-existing --dry-run
python run_sde_job.py --job final_watchlist --dry-run
```

`--dry-run` berarti job berjalan, tetapi Telegram tidak dikirim.

`--preview-existing` berarti hanya membuat preview dari data/output yang sudah ada.

## 3. Jalankan Manual Harian

Kalau mau klik file BAT:

```text
RUN_SDE_MARKET_OUTLOOK.bat
RUN_SDE_POST_MARKET.bat
RUN_SDE_FINAL_WATCHLIST.bat
```

Urutan normal:

```text
07:30  Market Outlook
16:30  Post Market
18:00  Final Watchlist
```

Jika mau menjalankan pipeline manual lama:

```text
RUN_SDE.bat
```

## 4. Alur yang Benar

Alur V1.3:

```text
Market Outlook
Post Market membuat technical snapshot
Final Watchlist menunggu broker summary valid
Final Watchlist dikirim hanya jika tanggal data cocok
```

Final Watchlist tidak akan dibuat kalau technical snapshot atau broker summary belum valid.

## 5. Cek Hasil

Status job:

```text
data/output/job_status/
```

Preview Telegram:

```text
data/output/previews/<tanggal>/
```

Failed delivery:

```text
data/output/failed_delivery/
```

Lock scheduler:

```text
data/state/scheduler/locks/
```

## 6. Pasang Task Scheduler

Generate XML dulu:

```bat
python generate_task_scheduler_xml.py
```

Lalu import file dari:

```text
scheduler/windows/generated/
```

Jika folder project dipindah, generate XML ulang.

## 7. Kalau Bingung Mulai dari Mana

Mulai dari tiga langkah ini:

```bat
install_requirements.bat
python run_sde_job.py --job market_outlook --dry-run
python run_sde_job.py --job post_market --preview-existing --dry-run
```

Setelah dua command test aman berhasil, baru lanjut pasang Task Scheduler.

