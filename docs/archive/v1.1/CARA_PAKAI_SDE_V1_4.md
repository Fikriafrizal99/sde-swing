# Cara Pakai SDE Swing V1.4 Global Market Yahoo

Ini versi ringkas untuk menjalankan paket V1.4.

## 1. Install Dependency

Dari root project:

```bat
install_requirements.bat
```

Atau:

```bat
pip install -r requirements.txt
```

Dependency penting untuk Global Market:

```text
yfinance
requests
pandas
numpy
```

## 2. Test Market Outlook Global

Jalankan:

```bat
python run_sde_job.py --job market_outlook --dry-run
```

Hasil yang perlu dicek:

```text
data/output/job_status/market_outlook_latest.json
data/output/global_market/<tanggal>/global_market_snapshot.json
data/output/previews/<tanggal>/market_outlook.txt
```

Kalau Yahoo berhasil, preview akan menampilkan:

```text
Global Market Snapshot
Global sentiment
Indeks Amerika
Indeks Asia
Currency
Komoditas
```

Kalau satu instrumen gagal, instrumen itu tampil:

```text
DATA_NOT_AVAILABLE
```

Job tetap lanjut.

## 3. Jalankan Scheduler Harian

Urutan normal:

```text
07:30  RUN_SDE_MARKET_OUTLOOK.bat
16:30  RUN_SDE_POST_MARKET.bat
18:00  RUN_SDE_FINAL_WATCHLIST.bat
```

Fallback manual:

```text
RUN_SDE.bat
```

## 4. Registry Simbol

Simbol global ada di:

```text
config/global_market.json
```

Jangan menambahkan provider lain. Jika simbol Yahoo tidak stabil, ubah:

```json
"enabled": false
```

Jangan isi angka manual atau dummy.

## 5. Yang Tidak Berubah

Global sentiment hanya konteks Market Outlook.

Global sentiment tidak mengubah:

```text
technical score
broker fusion
decision engine
final watchlist
exit engine
```

## 6. Pasang Task Scheduler

Generate ulang XML setelah folder project final dipilih:

```bat
python generate_task_scheduler_xml.py
```

Import XML dari:

```text
scheduler/windows/generated/
```

