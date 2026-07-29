# SDE Swing V1.3 Scheduler Hardening Report

Tanggal implementasi: 2026-07-24

## Paket

Nama paket final:

```text
SDE_SWING_V1_3_SCHEDULER_HARDENED.zip
```

Basis paket:

```text
SDE_SWING_V1_2_SCHEDULER_SAFE.zip
```

## Perubahan Utama

### 1. Post Market Technical-Only

Job `post_market` sekarang menjalankan tahap teknikal saja:

```text
historical_downloader
IHSG updater
technical_feature_engine
technical_candidate_selector
technical snapshot writer
```

Job ini tidak menjalankan:

```text
broker wait
broker fusion
decision engine
exit engine
final watchlist
Telegram final watchlist
```

### 2. Technical Snapshot

Post Market membuat snapshot teknikal versi tanggal berjalan di:

```text
data/output/snapshots/<trade-date>/
```

Manifest snapshot menyimpan:

- snapshot ID;
- trade date;
- file kandidat/ranking teknikal;
- broker symbol universe;
- timestamp pembuatan;
- mode dry-run atau production.

### 3. Final Watchlist Dari Snapshot

Job `final_watchlist` tidak lagi membangun ulang teknikal secara bebas. Job ini membaca snapshot tanggal yang sama, menunggu broker summary, memvalidasi broker, lalu baru menjalankan tahap final.

Jika broker belum ready sampai cutoff 18:30 WIB, default action adalah skip final watchlist dan kirim/siapkan data warning.

### 4. Broker Readiness Validator

Validator broker summary sekarang mengecek:

- file ada;
- file tidak kosong;
- file stabil dan tidak sedang ditulis;
- bisa diparse;
- kolom wajib tersedia;
- tanggal data sama dengan trade date job;
- tidak ada marker sample/fixture/dummy/placeholder;
- tidak ada duplikat simbol yang mencurigakan;
- nilai buy/sell numeric valid;
- coverage simbol memenuhi minimum config.

Hasil validator disimpan di:

```text
data/output/job_status/<run_id>_broker_readiness.json
data/output/job_status/<trade-date>/broker_readiness_latest.json
```

### 5. Dry Run dan Preview Existing

V1.3 membedakan dua mode:

```text
--dry-run
```

Menjalankan pipeline/job sungguhan tetapi tidak mengirim Telegram.

```text
--preview-existing
```

Memakai output yang sudah ada untuk membuat preview, cocok untuk cek format pesan tanpa menjalankan pipeline teknikal ulang.

### 6. Telegram Delivery

Perubahan Telegram:

- idempotency main report memakai `YYYY-MM-DD:REPORT_TYPE`;
- detail sinyal memakai `YYYY-MM-DD:SIGNAL_DETAIL:SYMBOL:STATUS:VERSION`;
- pesan panjang dipecah menjadi beberapa part tanpa memotong isi;
- delivery log menyimpan part count, message IDs, dan failed payload;
- helper HTML escape dipakai untuk payload yang butuh formatting aman.

### 7. Locking

Ada dua jenis lock:

- job lock, supaya job yang sama tidak berjalan ganda;
- global resource lock, supaya job penulis output tidak saling menimpa.

Lock tersimpan di:

```text
data/state/scheduler/locks/
```

### 8. Windows Task Scheduler XML

Generator baru:

```text
python generate_task_scheduler_xml.py
```

Output generated:

```text
scheduler/windows/generated/SDE_MARKET_OUTLOOK.xml
scheduler/windows/generated/SDE_POST_MARKET.xml
scheduler/windows/generated/SDE_FINAL_WATCHLIST.xml
```

Generator menulis XML UTF-8, memakai path absolut, dan memvalidasi hasil parse XML.

## Cara Operasional Harian

Urutan normal:

```text
07:30 WIB  RUN_SDE_MARKET_OUTLOOK.bat
16:30 WIB  RUN_SDE_POST_MARKET.bat
18:00 WIB  RUN_SDE_FINAL_WATCHLIST.bat
```

Fallback manual tetap tersedia:

```text
RUN_SDE.bat
```

## Status Implementasi

Selesai:

- technical-only post market;
- strict date validation;
- dry-run vs preview-existing;
- broker cutoff 18:30 WIB;
- missing broker policy;
- global resource lock;
- broker readiness validator;
- Telegram idempotency dan splitting;
- XML generator;
- richer job status;
- snapshot management;
- regression tests.

