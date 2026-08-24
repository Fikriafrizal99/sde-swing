# SDE Swing V1.6.2 Stage 1 Stabilization Report

> Archived historical implementation evidence. This dated snapshot is not
> current operational guidance. See `docs/README.md` for active documentation.

**Tanggal validasi:** 3 Agustus 2026  
**Versi kerja:** `1.6.2-stage1`  
**Pipeline version:** `SDE_SWING_V1_6_2_STAGE1_STABILIZED`  
**Mode validasi:** offline menggunakan data audit; tidak mengirim Telegram nyata.

## 1. Status penyelesaian

Tahap 1 **selesai dijalankan** pada salinan kerja baru. Baseline hasil rekonstruksi tidak ditimpa dan dicatat pada `BASELINE_REFERENCE_STAGE1.json`.

| Sasaran Tahap 1 | Status | Bukti |
|---|---|---|
| Baseline tidak ditimpa | Lulus | Git baseline `b1172df3db018b21ba03442b6b9907dafdeeaa6b` |
| Parser angka Stockbit konsisten | Lulus | Test format `65,12B`, `(2,41B)`, `2,186.59B`, `1.250,75K`, `8,750`, `850,5M` |
| Config runtime tunggal dan tervalidasi | Lulus | Hash `9ad377788a5e6749ff7aa8e8eabf69f4f67687bd48745227f0d2a8b7fd5d83d2` pada 30/30 baris keputusan |
| Override/migration lama tersembunyi ditolak | Lulus | `RuntimeConfigError` regression test |
| Decision trace dan rejected_by tersedia | Lulus | 0 field trace kosong pada decision dan entry-plan output |
| Decision owner tunggal | Lulus | 30/30 decision dan 11/11 plan dimiliki `DECISION_ENGINE` |
| Hanya BUY READY menjadi active trade | Lulus | Active: `BBRI`; final status `BUY READY` |
| Telegram kompatibel | Lulus | Dry-run menghasilkan 11 pesan tanpa error |
| Regression suite inti | Lulus | **116 passed, 0 failed** |

## 2. Perubahan yang diterapkan

### 2.1 Konfigurasi runtime

- Menambahkan `modules/runtime_config.py`.
- `config/pipeline.json` menjadi sumber konfigurasi utama dengan metadata versi dan hash.
- Runtime menghentikan proses bila versi source dan config tidak sama.
- Key override/migration/legacy tersembunyi ditolak.
- Setiap run menulis audit konfigurasi berisi sumber, SHA-256, waktu load, versi, dan nilai efektif.
- Candidate selector, master pipeline, job runner, serta decision engine menggunakan konfigurasi yang sama.
- Fallback laporan `technical_candidates_top30.csv` diperbaiki agar mengikuti `candidate.top` aktif.

### 2.2 Parser Stockbit

- Mendukung separator angka internasional dan Indonesia.
- Mendukung suffix K/M/B/T dan negatif akuntansi.
- Kasus `65,12B` tidak lagi berisiko terbaca 100 kali lebih besar.

### 2.3 Decision ownership dan telemetry

- Candidate selector hanya menentukan kandidat.
- Fusion/scoring menghasilkan komponen skor.
- Decision Engine menjadi pemilik kebijakan final.
- Exit Engine hanya memasok fakta entry plan dan memanggil state transition milik Decision Engine.
- Ditambahkan/diteruskan:
  - `Rejected_By`
  - `Hard_Blockers`
  - `Soft_Penalties`
  - `Decision_Trace`
  - `Decision_Owner`
  - `Final_Decision_Owner`
  - `Config_Source`, `Config_Hash`, `Config_Version`
- Alasan sementara `ENTRY_NOT_TRIGGERED` dibersihkan ketika plan sudah `ACCEPT` dan status menjadi `BUY READY`.

### 2.4 Active trade guard

- Hanya `Decision_Status_Final == BUY READY` yang dapat dimasukkan ke active trade.
- `BUY ON TRIGGER`, `WATCH`, dan `AVOID` tidak membuka posisi aktif.
- `WATCH` dengan plan teknis `ACCEPT` tetap `WATCH`; Exit Engine tidak boleh menaikkan statusnya sendiri.

### 2.5 Telegram

- Direct script import diperbaiki agar project root selalu tersedia.
- Rekap Telegram membaca `Decision_Status_Final` dari entry plan.
- Alias UI lama tetap dipertahankan:
  - `BUY READY` → `BUY CONFIRMED`
  - `BUY ON TRIGGER` → `BUY CANDIDATE`
- Dry-run akhir konsisten dengan output engine.

## 3. Hasil validasi offline

### 3.1 Decision Engine sebelum entry plan

| Status | Jumlah |
|---|---:|
| BUY ON TRIGGER | 10 |
| WATCH | 12 |
| AVOID | 8 |
| Total | 30 |

### 3.2 Setelah entry plan

| Status final plan | Jumlah |
|---|---:|
| BUY READY | 1 |
| BUY ON TRIGGER | 9 |
| WATCH | 1 |
| Total plan | 11 |

`BBRI` menjadi satu-satunya active trade pada validasi ini karena plan berstatus `ACCEPT` dan keputusan final `BUY READY`. Kandidat lain tidak dipaksa masuk posisi.

### 3.3 Telegram dry-run

Rekap akhir:

- BUY CONFIRMED: 1
- BUY CANDIDATE: 9
- WATCH: 12
- AVOID: 8
- Live send: tidak dilakukan

## 4. Gate yang paling sering tercatat

| Reason | Jumlah |
|---|---:|
| `BROKER_DIVERGENCE` | 19 |
| `ENTRY_NOT_TRIGGERED` | 10 |
| `BROKER_DISTRIBUTION` | 7 |
| `LOW_COMPOSITE_SCORE` | 4 |
| `LIQUIDITY_VERY_POOR` | 3 |
| `STRONG_BROKER_DISTRIBUTION` | 2 |
| `PRICE_EXTENDED_SOFT` | 2 |
| `PRICE_EXTENDED_HARD` | 1 |

Jumlah ini adalah telemetry dari dataset validasi, bukan rekomendasi otomatis untuk mengubah threshold. Evaluasi logika dan kalibrasi gate masuk Tahap 2.

## 5. Test dan pemeriksaan

- `python -m compileall -q .`: lulus.
- `git diff --check`: lulus.
- `pytest -q`: **116 passed**.
- Decision → Exit Engine offline: lulus.
- Telegram swing dry-run: lulus.
- Config hash consistency: 30/30 baris.
- Decision trace kosong: 0.
- Final decision owner selain Decision Engine: 0.
- Active trade bukan BUY READY: 0.

Folder `payload/tests` tidak dimasukkan ke suite inti karena paket payload yang dibawa audit merupakan komponen lain dan tidak lengkap (`orchestrator` tidak tersedia). Ini tidak disamarkan sebagai test lulus.

## 6. Batas validasi

- Belum menjalankan sumber live, ZAPI IDX, atau Stockbit live session.
- Belum mengirim Telegram nyata.
- Token/API key tetap redacted atau dibaca dari environment variable.
- Paket audit memiliki beberapa fragmen executable yang ter-redact; bagian non-secret yang deterministik dipulihkan agar source dapat dikompilasi, tetapi credential tidak direkonstruksi.
- Tahap 2 (kalibrasi logika per setup) dan Tahap 3 (multi-source Stockbit + ZAPI IDX) belum dijalankan.

## 7. Cara menjalankan validasi lokal

```bash
pytest -q
python modules/decision_engine/decision_engine.py \
  data/input/FINAL_DECISION_V2.csv \
  data/output/stage1_validation/decision \
  --ihsg data/input/IHSG.csv \
  --config config/pipeline.json
```

Telegram sebaiknya diuji lebih dahulu dengan `--dry-run`; token nyata diletakkan di environment variable `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`, bukan di source.

## 8. Kesimpulan

Tahap 1 memenuhi tujuan stabilisasi: konfigurasi runtime dapat diaudit, parser numerik terlindungi oleh regression test, final decision memiliki satu pemilik, telemetry konsisten, dan active-trade guard berjalan. Versi ini layak menjadi fondasi untuk **Tahap 2**, tetapi belum merupakan validasi live atau izin auto-entry.
