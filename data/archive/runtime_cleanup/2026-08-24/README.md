# Arsip pembersihan runtime — 2026-08-24

Arsip ini menyimpan data yang tidak diperlukan oleh eksekusi SDE saat ini, tetapi masih dapat dipulihkan untuk audit atau replay. Semua isi ZIP telah dibandingkan dengan sumber menggunakan ukuran dan SHA-256 sebelum sumber aktif dihapus.

## Data yang dipertahankan aktif

- Database utama: `data/database/sde_swing_history.db`
- Database histori broker: `data/database/broker_multiday.db`
- Canonical run terbaru: `SDE-POST-MARKET-20260823-211302-5d58`
- Seluruh data lifecycle/performance, manifest, snapshot operasional, state Telegram, dan profil login Stockbit

## Inventaris arsip

| Arsip | Isi sumber | File | Byte sumber | Byte ZIP | SHA-256 ZIP |
|---|---|---:|---:|---:|---|
| `audit_sde_swing_history_snapshot.db.zip` | Snapshot audit DB 2026-08-14 | 1 | 618074112 | 195244897 | `17A9137A90378513BB46434420A80F974B72105AE1B5BE37FE985D4AC1349838` |
| `historical_validated_runs_20260810_20260813.zip` | 10 validated run lama | 7843 | 263885330 | 51934859 | `C3B4CB417F402026F63A3EDEC59FF7F92DAFB5C5A1F3418AEDC6C8FCB7586DA1` |
| `historical_canonical_runs_20260813_20260820.zip` | 6 canonical run lama | 4743 | 158747000 | 31354304 | `AD694C05054F232532E84D445BCCD2B588EFC202FF6CBF4DBF5F0A3F616148BA` |

SHA-256 asli snapshot database: `5E748F2278FC6799FF1FFACD6B3B75A8009CB0DD2BE4A564A6F43365FD6C83AB`.

Validated run yang diarsipkan:

- `SDE-POST-MARKET-20260810-173328-3dbe`
- `SDE-POST-MARKET-20260811-163018-4d18`
- `SDE-POST-MARKET-20260811-181047-0414`
- `SDE-POST-MARKET-20260812-163019-41ec`
- `SDE-POST-MARKET-20260812-165133-7db6`
- `SDE-POST-MARKET-20260812-182840-9f04`
- `SDE-POST-MARKET-20260812-195105-cc75`
- `SDE-POST-MARKET-20260812-202717-a083`
- `SDE-POST-MARKET-20260813-162216-edc8`
- `SDE-POST-MARKET-20260813-162840-bdcb`

Canonical run yang diarsipkan:

- `SDE-POST-MARKET-20260813-155839-f387`
- `SDE-POST-MARKET-20260814-163402-797b`
- `SDE-POST-MARKET-20260818-163023-f69d`
- `SDE-POST-MARKET-20260819-121630-785c`
- `SDE-POST-MARKET-20260819-164548-6b36`
- `SDE-POST-MARKET-20260820-163004-c0ab`

## Cara memulihkan

Hentikan scheduler/pipeline sebelum pemulihan. Verifikasi hash ZIP dengan `Get-FileHash -Algorithm SHA256`, lalu:

- Ekstrak snapshot DB ke `data/database/`.
- Ekstrak validated runs ke `data/output/historical/validated_runs/`.
- Ekstrak canonical runs ke `data/output/historical/canonical_runs/`.

ZIP run menyimpan path relatif berupa `<run_id>/<symbol>.csv`, sehingga dapat langsung diekstrak ke folder kategori masing-masing.
