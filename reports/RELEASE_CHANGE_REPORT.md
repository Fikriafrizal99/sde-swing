# Release Change Report — SDE Swing V1.2 Safe Baseline

## Release decision

Status: **APPROVED FOR SAFE BASELINE PACKAGING**

Paket baru hanya dibuat setelah dependency audit, source cleanup, regression test,
modular E2E test, dan canonical master pipeline replay selesai.

## Kondisi sebelum perbaikan

- Terdapat dua Decision Engine identik dan dua preprocessor identik.
- Ada dua jalur pipeline: `run_pipeline.py` lama dan `master_pipeline.py`.
- Manifest lama tidak cocok dengan isi ZIP.
- Snapshot broker lama hanya cocok 17/30 kandidat atau 57%.
- V2 lama berstatus `STALE_ACCEPTED`.
- Ranking penuh dapat menempatkan FILTERED sebelum PASS.
- Exit Engine memiliki beberapa bug trigger dan canonical column.
- Fixture test masih mencoba memanggil updater IHSG live.
- Universe downloader mencampur empat simbol non-equity.

## Perubahan material

### Pipeline quality

- Satu canonical pipeline dan satu canonical Decision Engine.
- Full run menjadi default launcher utama.
- Existing-data mode dipisahkan dan diberi coverage gate.
- Offline release validator ditambahkan.

### Decision consistency

Decision Engine V3 dihitung ulang secara independen dalam validator untuk:

- `Liquidity_Score`;
- `Final_Score_V3`;
- `Liquidity_Class`;
- `Decision_V3`.

Hasil: **30 row diperiksa, 0 mismatch** pada modular E2E dan **30 row, 0 mismatch**
pada canonical master pipeline replay.

### Exit correctness

- BEAR gate diperbaiki.
- Decision downgrade menjadi hard exit.
- Dynamic max hold diterapkan.
- Ambiguous intraday bar memakai stop-first conservative rule.
- Duplicate output columns dihilangkan.

### Data safety

- Broker coverage/tanggal divalidasi sebelum fusion.
- Test terhadap snapshot lama gagal sesuai desain:

```text
Broker coverage 17/30 (57%) di bawah minimum 80%
```

- Baseline tidak membawa broker summary atau V2 lama agar user wajib membangun
  snapshot yang sinkron dengan kandidat run baru.

## End-to-end result

| Pemeriksaan | Hasil |
|---|---:|
| Python compile | PASS |
| Regression | 32/32 PASS |
| Historical files evaluated | 441 |
| Technical success | 439 |
| Technical skipped | 2 short-history symbols |
| Candidates | 30 |
| Broker coverage | 100% |
| Decision contract | 30/30, 0 mismatch |
| Exit Engine | PASS |
| Analytics | PASS |
| Database archive | PASS |
| Telegram dry-run | PASS |
| Master pipeline orchestration | SUCCESS |

Detail tersedia di `reports/E2E_VALIDATION_REPORT.md` dan `reports/e2e_logs/`.

## Batas validasi

- Yahoo live network tidak dipanggil pada release validation agar hasil deterministik.
- Telegram tidak benar-benar mengirim pesan; mode dry-run dipakai.
- Broker fixture deterministik digunakan untuk memverifikasi contract dan coverage.
- Live run tetap membutuhkan internet, export Broker Navigator, dan konfigurasi
  Telegram milik user.

## Packaging rule

Manifest SHA-256 harus dibuat setelah semua report selesai dan setelah cache
terakhir dibersihkan. ZIP dianggap valid hanya jika seluruh hash manifest dapat
diverifikasi ulang tanpa mismatch melalui `RUN_VERIFY_MANIFEST.bat`.
