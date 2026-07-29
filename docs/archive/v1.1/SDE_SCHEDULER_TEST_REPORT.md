# SDE Swing V1.3 Scheduler Test Report

Tanggal uji: 2026-07-24

## Environment

Runtime yang dipakai saat uji:

```text
C:\Users\Moch Fikri Arrizal\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

Catatan dependency:

- `requirements.txt` berisi `pandas`, `numpy`, `yfinance`, dan `requests`.
- Runtime uji lokal ini belum memiliki `yfinance` dan `requests`, sehingga live data download dan live Telegram tidak dijalankan penuh.

## Unit dan Regression Test

Command:

```bat
python -m unittest discover -s tests -p "test*.py" -v
```

Hasil:

```text
57 tests OK
```

Coverage hardening yang diuji:

- global market Yahoo-only registry, snapshot, cache, retry, freshness, sentiment;
- `post_market --dry-run` menjalankan technical stage, bukan master pipeline lama;
- `--preview-existing` tidak menjalankan technical stage;
- stale technical snapshot ditolak;
- broker summary beda tanggal ditolak;
- broker summary tanggal sama diterima;
- schema broker salah ditolak;
- sample broker data ditolak;
- duplicate broker data ditolak;
- numeric broker data invalid ditolak;
- missing broker default policy skip final watchlist;
- preliminary watchlist policy tersedia;
- global resource lock memblokir writer kedua;
- idempotency Telegram main report berbasis report type/tanggal;
- force resend tetap tersedia;
- Telegram splitter tidak menghilangkan isi;
- generated Task Scheduler XML valid dan bebas placeholder.

## Dry Run Command

### Market Outlook

Command:

```bat
python run_sde_job.py --job market_outlook --dry-run --trade-date 2026-07-24
```

Hasil:

```text
exit code 0
status SUCCESS
Telegram SKIPPED
```

### Post Market Technical Pipeline

Command:

```bat
python run_sde_job.py --job post_market --dry-run --trade-date 2026-07-24
```

Hasil:

```text
technical stage dijalankan
exit code 1
status FAILED
error POST MARKET HISTORICAL DOWNLOADER gagal
```

Penyebab di environment uji:

```text
Dependency yfinance belum terpasang. Jalankan: pip install -r requirements.txt
```

Ini justru membuktikan `--dry-run` V1.3 tidak lagi hanya membaca preview existing. Job benar-benar masuk ke pipeline teknikal, tetapi berhenti karena dependency live-data belum tersedia di runtime uji.

### Post Market Preview Existing

Command:

```bat
python run_sde_job.py --job post_market --preview-existing --dry-run --trade-date 2026-07-24
```

Hasil:

```text
exit code 0
status SUCCESS
Telegram SKIPPED
preview closing bell dan daily signal recap tersedia
```

### Final Watchlist

Command:

```bat
python run_sde_job.py --job final_watchlist --dry-run --trade-date 2026-07-24
```

Hasil:

```text
status INVALID_DATA
reason TECHNICAL_SNAPSHOT_NOT_FOUND
final watchlist tidak dibuat
data warning dibuat
```

Perilaku ini sesuai guardrail V1.3: final watchlist harus menunggu technical snapshot tanggal yang sama dan broker summary valid.

## Task Scheduler XML

Command:

```bat
python generate_task_scheduler_xml.py
```

Hasil:

```text
generated 3 XML files
XML parse valid
tidak ada __PROJECT_DIR__
```

## Kesimpulan

Hardening V1.3 lulus regression test lokal. Live execution tetap perlu dijalankan di mesin operasional setelah dependency dari `requirements.txt` terpasang dan kredensial Telegram sudah dikonfigurasi.
