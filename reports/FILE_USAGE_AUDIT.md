# File Usage Audit — SDE Swing V1.2 Safe Baseline

Tanggal audit: **24 Juli 2026**  
Target versi: `SDE_SWING_V1_2_SAFE_BASELINE`

## Metode audit

Setiap file diperiksa melalui:

1. pencarian import, subprocess command, konfigurasi path, dan launcher;
2. perbandingan SHA-256 untuk mendeteksi duplikat byte-for-byte;
3. pemeriksaan jalur produksi `master_pipeline.py`;
4. pemeriksaan pemakaian manual/eksternal seperti Tampermonkey dan Telegram;
5. eksekusi regression dan end-to-end setelah pembersihan.

File hanya dihapus bila terbukti duplikat, menunjuk jalur lama, sudah digantikan
komponen aktif, atau merupakan snapshot runtime yang tidak aman digunakan ulang.

## Jalur resmi yang dipertahankan

| Komponen | File utama | Status |
|---|---|---|
| Orkestrasi | `master_pipeline.py` | Jalur produksi tunggal |
| Shared contract/version | `swing_utils.py` | Aktif |
| Yahoo historical | `modules/historical_downloader/historical_downloader.py` | Aktif |
| IHSG | `modules/market_data/update_ihsg.py` | Aktif pada live mode |
| Technical feature | `modules/technical_feature_engine/technical_feature_engine.py` | Aktif |
| Candidate selector | `modules/candidate_selector/technical_candidate_selector.py` | Aktif |
| Broker Navigator export | `modules/broker_bridge/broker_navigator_export.py` | Aktif |
| Broker wait/import | `modules/broker_bridge/wait_for_broker_export.py` | Aktif |
| Broker fusion | `modules/broker_fusion/broker_fusion.py` | Aktif |
| Decision V3 | `modules/decision_engine/decision_engine.py` | Satu-satunya Decision Engine aktif |
| Entry/exit | `modules/exit_engine/exit_engine.py` | Aktif |
| Analytics | `modules/backtesting/backtest_engine.py` | Aktif |
| Database archive | `modules/database/swing_history_db.py` | Aktif |
| Telegram | `modules/telegram/*.py` | Aktif |
| Upstream manual preprocessing | `modules/data_preprocessor/stockbit_preprocessor.py` | Dipertahankan untuk regenerasi normalized watchlist |
| Broker browser automation | `tampermonkey/Stockbit_Broker_Summary_Auto_Navigator_v3.1.user.js` | Dipertahankan; versi komponen independen |

## File yang dihapus

### Duplikat identik

| Dihapus | Canonical yang dipertahankan | Alasan |
|---|---|---|
| `modules/decision_engine/decision_engine_v3_1.py` | `modules/decision_engine/decision_engine.py` | Isi identik; mencegah dua entry point Decision Engine |
| `modules/preprocessor/stockbit_preprocessor.py` | `modules/data_preprocessor/stockbit_preprocessor.py` | Isi identik |

### Jalur eksekusi lama atau parsial

- `run_pipeline.py`
- `RUN_FUSION_AND_DECISION.bat`
- `RUN_SDE_REFRESH_DATA.bat`
- `run_daily_signal.bat`
- `run_decision_engine.bat`

File tersebut dihapus karena membentuk jalur selain `master_pipeline.py`, memakai
nama versi lama, atau dapat menjalankan keputusan dari snapshot yang belum melalui
validasi freshness dan coverage terbaru.

### Fungsi yang sudah digantikan

- `modules/decision_source_builder/build_decision_source.py`
- `modules/decision_source_builder/refresh_decision_source.py`
- `modules/backtesting/archive_daily_signal.py`

Decision source builder sudah digantikan oleh Broker Fusion yang memiliki validasi
coverage/tanggal. Archive harian sudah ditangani oleh run manifest dan SQLite
archive.

### Launcher/dependency modul lokal yang usang

- `modules/candidate_selector/run_selector.bat`
- `modules/historical_downloader/run_downloader.bat`
- `modules/technical_feature_engine/run_feature_engine.bat`
- `modules/technical_feature_engine/run_feature_engine_with_timeseries.bat`
- tiga `requirements.txt` lokal modul

Semua dependency sudah dikonsolidasikan di root `requirements.txt`; launcher lokal
menggunakan struktur folder lama dan tidak menjadi bagian jalur produksi.

### Runtime snapshot tidak aman

- `data/input/FINAL_DECISION_V2.csv` dan manifest lama;
- `data/input/broker/BROKER_SUMMARY_LATEST.csv` dan manifest lama;
- tiga broker archive CSV lama.

Snapshot broker lama hanya cocok dengan **17 dari 30 kandidat (57%)**, di bawah
minimum 80%. `FINAL_DECISION_V2.csv` lama berasal dari run
`SWING-20260721-175034-8482` dengan status `STALE_ACCEPTED`. Baseline sekarang
memaksa full run membangun ulang file-file tersebut.

### Lainnya

- file kosong `git`;
- cache Python/pytest dibersihkan setelah validasi akhir.

## Dokumentasi lama

Dokumentasi V1.1 tidak dibuang. Semua dipindahkan ke:

```text
docs/archive/v1.1/
```

Dokumen tersebut hanya riwayat audit dan tidak lagi menjadi panduan operasional.

## Launcher aktif setelah audit

- `RUN_SDE.bat` — full pipeline produksi;
- `RUN_SDE_EXISTING_DATA.bat` — reuse V2 yang sudah valid;
- `RUN_RELEASE_VALIDATION.bat` — offline E2E validation;
- `RUN_VERIFY_MANIFEST.bat` — verifikasi checksum dan file tidak tercatat;
- `run_test_telegram.bat`;
- `run_daily_telegram.bat`;
- `get_chat_id.bat`;
- `install_requirements.bat`.

## Kesimpulan

Tidak ada source utama, historical by-symbol, database utama, test fixture,
konfigurasi, atau integrasi Telegram/Tampermonkey yang dihapus. Pembersihan hanya
menghilangkan duplikasi, jalur usang, dan state runtime yang berisiko mencampur
run lama dengan kandidat baru.
