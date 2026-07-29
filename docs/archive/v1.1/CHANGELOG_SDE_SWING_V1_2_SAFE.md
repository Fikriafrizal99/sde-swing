# CHANGELOG — SDE Swing V1.2 Safe Baseline

Release date: **24 Juli 2026**  
Package version: `1.2.0`  
Pipeline version: `SDE_SWING_V1_2_SAFE_BASELINE`

## Version and architecture

- Semua identitas SDE aktif disamakan ke V1.2 Safe Baseline.
- `master_pipeline.py` menjadi satu-satunya jalur pipeline resmi.
- Decision Engine aktif disatukan ke `modules/decision_engine/decision_engine.py`.
- Dokumentasi V1.1 dipindahkan ke archive, bukan digunakan sebagai panduan aktif.
- Versi Tampermonkey v3.1 tetap dipertahankan sebagai versi komponen browser yang
  independen dari versi paket SDE.

## Candidate and historical fixes

- Candidate ranking kini menempatkan `PASS` sebelum `FILTERED`.
- Kontrak feature disambungkan ke nama aktual Technical Feature Engine.
- `Distance_High_252_Pct` diprioritaskan untuk representasi 52-week high.
- `Slope_Close_20` diprioritaskan sebagai trend slope.
- `Turnover_MA_20` mengaktifkan liquidity filter yang sebelumnya terputus.
- Historical Downloader mengecualikan simbol non-equity `IHSG`, `BRENT`, `OIL`,
  dan `XAU`; IHSG tetap dikelola oleh updater khusus.
- Waktu evaluasi dapat dibekukan untuk replay/test deterministik.
- Fixture mode tidak lagi memanggil IHSG updater lewat jaringan.

## Broker and data-quality fixes

- Broker Fusion memvalidasi expected symbols, duplicate, coverage, dan tanggal.
- Coverage di bawah minimum menghentikan pipeline kecuali override eksplisit.
- Existing V2 mode memvalidasi coverage sebelum Decision Engine dijalankan.
- Snapshot broker/V2 lama dengan coverage 57% dihapus dari baseline.

## Decision and Exit fixes

- Formula dan threshold Decision V3 tidak diubah.
- Release validation menghitung ulang score, liquidity class, dan decision untuk
  setiap row; hasil cocok 30/30.
- Exit loader tidak lagi menghasilkan duplicate canonical columns.
- Market regime `BEAR` dan `BEARISH` sama-sama menolak entry baru.
- Decision downgrade ke AVOID/SPECULATIVE menutup trade aktif.
- `max_hold_days` mengikuti parameter, tidak lagi hardcoded.
- Stop diprioritaskan secara konservatif bila stop dan target tersentuh pada candle
  yang sama.

## Telegram/report safety

- Plan `REJECT` tidak ditampilkan sebagai entry aktif.
- Label liquidity tidak lagi ditulis sebagai Risk.
- Performance report hanya dibuat bila ada outcome final.
- Exit report kosong tidak dibuat.

## Cleanup

- Duplikat Decision Engine dan preprocessor dihapus.
- Decision source builder lama, archive helper lama, dan launcher parsial dihapus.
- Dependency dikonsolidasikan ke root `requirements.txt`.
- Cache dan output runtime lama dibersihkan.
- Manifest generator dan self-verifier ditambahkan.
- Dokumentasi audit lama dipindahkan ke `docs/archive/v1.1/`.

## Validation

- Regression tests: **32/32 PASS**.
- Technical Feature Engine: **439/441 success**; JECX dan JELI dilewati karena
  hanya memiliki 11 baris historical.
- Candidate count: **30**.
- Broker coverage fixture: **100%**.
- Decision contract: **30/30 match, 0 mismatch**.
- Canonical `master_pipeline.py`: **SUCCESS** dalam OFFLINE_FIXTURE mode.
- Exit, analytics, database archive, dan Telegram dry-run: **PASS**.
