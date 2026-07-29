# Regression Test Report Swing V1.1

## Test Command

Runtime yang dipakai pada implementasi:

```bat
C:\Users\Moch Fikri Arrizal\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest tests.test_swing_v1_1
```

## Result

```text
Ran 24 tests
OK
```

## Status Test

| Test | Status | Metode |
|---|---:|---|
| Yahoo partial candle validation | PASS | Offline fixture |
| Yahoo provider failure | PASS | Simulation/offline fixture path |
| Yahoo fixture source transparency | PASS | Offline fixture |
| Yahoo first-run full backfill planning | PASS | Unit/offline pre-check |
| Yahoo already-current skip without network | PASS | Unit/offline pre-check |
| Yahoo incremental overlap window | PASS | Unit/offline pre-check |
| Yahoo multiple same-day second-run skip | PASS | Offline fixture CLI |
| Yahoo failed/new symbol retry planning | PASS | Unit/offline pre-check |
| Yahoo partial-candle incremental retry | PASS | Unit/offline pre-check |
| Yahoo batch response split | PASS | Unit/fake provider |
| Yahoo batch failure fallback to individual | PASS | Unit/fake provider |
| Yahoo force-refresh incremental behavior | PASS | Unit/offline pre-check |
| Yahoo explicit full-backfill behavior | PASS | Unit/offline pre-check |
| Yahoo canonical historical columns | PASS | Unit/offline merge |
| Yahoo live network refresh | NOT RUN | Network/provider live tidak dijalankan |
| Technical freshness validation | PASS | Local validation layer |
| Technical Feature Engine baseline run | PASS | Local integration, 439 success / 2 short-data warnings |
| Candidate unchanged hash | PASS | Offline fixture |
| Broker partial export 15 + 15 | PASS | Offline broker fixture |
| Broker date mismatch/manual file | PASS | Offline broker fixture |
| Broker missing/unexpected/duplicate/incompatible fixtures | PASS | Offline broker fixture |
| Broker navigator locked output fallback | PASS | Simulation |
| Tampermonkey live export | NOT RUN | Membutuhkan browser, login, dan export manual |
| Decision Engine regression | PASS | Local integration/compile |
| Exit Engine regression | PASS | Local integration/compile |
| Swing Analytics backtest loader collision | PASS | Local integration |
| Database deduplication | PASS | Unit/offline fixture |
| Database archive full historical smoke | PASS | Local integration, 441 files / 207k rows |
| Database archive unchanged file skip | PASS | Local idempotency check, 441/441 skipped |
| Telegram dry run | PASS | Local integration |
| Telegram live send | NOT RUN | Credential/send dinonaktifkan |

## Coverage Fixture

- Partial Yahoo candle tidak dihitung valid refresh.
- Yahoo current local data di-skip tanpa network request.
- Yahoo incremental memakai safety overlap dan tidak berubah menjadi full backfill.
- Batch Yahoo dapat memisahkan hasil per simbol dan fallback ke individual saat batch gagal.
- Candidate unchanged dicatat dengan `RESULT_IDENTICAL_AFTER_FRESH_RECALCULATION`.
- Broker coverage 30/30 untuk skenario union 15+15.
- Manual broker file dengan tanggal berbeda mencatat `BROKER_DATE_OVERRIDE`.
- Missing symbol, unexpected symbol, duplicate symbol, dan incompatible batch terdeteksi lewat fixture.
- SQLite canonical prices tidak menggandakan row harga yang sama.
- SQLite archive memakai batch upsert dan marker file hash untuk skip file historical yang belum berubah.
- Telegram dry-run menulis preview TXT dan tetap terbaca tanpa emoji.
- Chunking Telegram tidak memotong blok saham.

## Catatan Lingkungan

Live Yahoo refresh dan live Stockbit/Tampermonkey wait tidak dijalankan di environment ini karena membutuhkan network dan tindakan browser/user. Flow tersebut sudah diberi test offline di layer validasi dan CLI policy.

Hasil ini adalah:

```text
OFFLINE VALIDATION PASSED
```

Bukan:

```text
LIVE YAHOO REFRESH PASSED
LIVE STOCKBIT END-TO-END PASSED
FULL LIVE END-TO-END TEST PASSED
```

Pengujian live tetap wajib dilakukan pada environment operasional sebelum status dapat dinaikkan menjadi production live verified.
