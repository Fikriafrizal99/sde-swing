# SDE Swing V1.5 Telegram UI Test Report

Tanggal uji: 2026-07-24

## Unit dan Regression Test

Command:

```bat
python -m unittest discover -s tests -p "test*.py" -v
```

Hasil:

```text
65 tests OK
```

Test mencakup seluruh test baseline V1.4 ditambah 8 test UI baru.

## Test UI Baru

Skenario yang diuji:

- satu hasil final per simbol unik;
- duplicate symbol tidak dihitung dua kali;
- kondisi tanpa sinyal tidak membagi dengan nol;
- signal bar maksimal 20 emoji;
- entry, TP, dan SL tidak dibuat jika plan tidak valid;
- watchlist maksimal lima kandidat dan AVOID tidak masuk;
- stale warning memiliki penjelasan manusia;
- stale warning menjelaskan dampak terhadap keputusan;
- dynamic HTML value di-escape;
- plan `REJECT` tampil sebagai `ENTRY READINESS: NOT READY`;
- no-emoji mode jalur lama tetap berfungsi;
- Telegram splitter tetap mempertahankan seluruh isi.

## Dry Run

### Legacy / Full Manual Telegram

Command menggunakan `modules/telegram/telegram_bot.py --dry-run swing`.

Hasil:

```text
exit code 0
Telegram tidak dikirim
preview report tersimpan
```

### Scheduler Post Market

Command:

```bat
python run_sde_job.py --job post_market --preview-existing --dry-run --trade-date 2026-07-24 --force
```

Hasil:

```text
exit code 0
Telegram SKIPPED_DRY_RUN
Closing Bell preview tersedia
Rekap Sinyal Harian preview tersedia
```

## Preview Acceptance

Tujuh contoh laporan disimpan di:

```text
data/output/telegram_ui_preview/scheduled/
```

Semua contoh berada di bawah batas 4.000 karakter per pesan.

## Catatan

Preview memakai data paket yang tersedia. Tanggal data dapat berbeda dari tanggal pembuatan preview bila menggunakan `--preview-existing`; UI tidak mengganti tanggal tersebut dengan tanggal buatan.

## Extended E2E Validation

Sepuluh tahap pertama berhasil:

```text
Python compile
65 unit/regression tests
Technical Feature Engine
Candidate Selector
Broker Fusion
Decision Engine
Exit Engine
Swing Analytics
Database Archive
Telegram UI dry run
```

Tahap `Master pipeline orchestration` tidak selesai sebelum batas waktu execution environment. Karena itu, full master orchestration tidak diklaim lulus pada laporan ini. Detail tersedia di `reports/E2E_VALIDATION_REPORT.md`.
