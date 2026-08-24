# Broker Period Architecture

## Tujuan

Final Watchlist memiliki satu interpretasi broker produksi: hasil Broker Fusion
dari periode Stockbit exact yang dipilih operator. Desain ini mencegah data
harian lokal yang tidak lengkap menghasilkan opini broker kedua, score sintetis,
atau konfirmasi semu.

```text
Exact Stockbit PRIMARY
          │
          ▼
Broker Summary / Broker Fusion ──► PRIMARY facts
          │
          ├── (PRIMARY > 1D) Exact Stockbit 1D hari ini ──► TODAY pulse
          │                                                   │
          └───────────────────────────────────────────────────┘
                              │
                              ▼
                    Alignment (presentation only)
                              │
                              ▼
                   Final Watchlist / Watchlist AI
```

Broker Fusion tetap pemilik `broker_score` dan `broker_state`. Decision Engine
tetap pemilik keputusan, `Decision_Status_Final`, dan `Final_Score_V3`.

## Mengapa database rolling retired dari Final Watchlist

Database daily broker tidak memiliki cakupan simbol yang konsisten: sebuah
simbol yang tidak masuk navigator/watchlist pada suatu hari tidak memiliki
record, tetapi itu bukan bukti bahwa broker tidak beraktivitas. Karena itu,
rolling 3D/5D/10D/20D dari database tidak boleh menggantikan atau menilai ulang
export Stockbit exact.

Database/history dapat tetap dipakai untuk arsip, audit, dan fitur portfolio
yang berdiri sendiri. Ia tidak menghasilkan secondary broker opinion, score,
atau input keputusan Final Watchlist.

## Definisi periode

| Konsep | Definisi | Pemakaian |
| --- | --- | --- |
| PRIMARY | Export Stockbit exact 1D, 3D, 5D, atau CUSTOM yang dipilih operator | Satu-satunya sumber broker produksi untuk Final Watchlist |
| TODAY pulse | Export Stockbit exact 1D pada tanggal Final Watchlist | Context presentasi bila PRIMARY lebih dari 1D |
| Alignment | Label perbandingan net flow PRIMARY dan TODAY | Context presentasi; tidak mengubah score/keputusan |

### PRIMARY = 1D

- Capture 1D diaktifkan sebagai PRIMARY.
- Tidak ada TODAY pulse kedua dan tidak ada penggandaan fakta 1D.
- `today_pulse_status=NOT_APPLICABLE`; alignment dapat kosong.

### PRIMARY = 3D, 5D, atau CUSTOM

- Sistem capture exact 1D hari ini untuk TODAY pulse.
- Sistem capture exact aggregate PRIMARY untuk periode yang dipilih.
- Snapshot summary dan raw PRIMARY diaktifkan sebelum Broker Summary/Broker
  Fusion berjalan.
- TODAY tidak menggantikan PRIMARY; ia hanya mengisi field `today_*` dan
  alignment.

## Source-of-truth matrix

| Fakta Final Watchlist | Sumber otoritatif |
| --- | --- |
| `broker_score`, `broker_state`, `broker_direction` | Broker Fusion dari PRIMARY |
| `broker_net_flow`, AccDist, concentration | Exact PRIMARY summary |
| Top buyer/seller | Exact PRIMARY raw snapshot yang disebut sidecar aktif |
| Average buyer/seller cost dan distance | Exact PRIMARY/Fusion lineage |
| `today_*` | Exact 1D TODAY snapshot, hanya bila PRIMARY > 1D |
| `broker_alignment` | PRIMARY-vs-TODAY presentation comparison |
| `Decision_Status_Final`, `Final_Score_V3` | Decision Engine yang sudah ada |

## No reconstructed multi-day rule

Final Watchlist tidak membaca atau menghitung ulang:

- `BROKER_MULTIDAY_SUMMARY.csv`, `BROKER_MULTIDAY_DETAIL.csv`, atau
  `BROKER_WINDOW_COMPARISON.csv`;
- rolling/reconstructed 3D/5D/10D/20D dari database;
- `Broker_MultiDay_Score`, `Broker_MultiDay_Context`, persistence,
  `multi_day_flow`, atau `buy_days`/`sell_days` hasil histori;
- synthetic 0/100 akibat missing coverage database.

Tidak ada fallback diam-diam dari raw canonical TODAY ke raw PRIMARY. Ketika
sidecar periode aktif, `primary_raw_snapshot_path` harus tersedia dan valid;
ketiadaan path itu adalah kondisi fail-closed bagi flow wrapper produksi.

## Arsip, history, dan portfolio

Daily 1D capture dapat diarsipkan untuk audit karena provenance-nya berguna.
Snapshot archive tidak menjadi input scoring atau presentasi Final Watchlist.

`modules/portfolio/broker_history_context.py` tetap diizinkan untuk posisi OPEN
portfolio. Use case tersebut terpisah dari kandidat Final Watchlist dan tidak
boleh menulis atau menyuntikkan context kembali ke `FINAL_DECISION_V2`/V3.

Modul legacy database/multi-day yang tersisa adalah compatibility/archive-only;
mereka tidak terdaftar sebagai job runtime dan tidak menjadi dependency Final
Watchlist.

## Final Watchlist data contract

Row Final Watchlist membawa:

- metadata PRIMARY: `broker_period_type`, start/end, session dates, snapshot
  ID, source, coverage, dan freshness;
- fakta PRIMARY: flow, concentration, participant raw, cost, dan state/score
  Fusion;
- metadata TODAY yang eksplisit: `today_pulse_available`, status, date,
  net flow, state/AccDist/concentration/participants bila tersedia;
- `broker_alignment` sebagai label presentasi saja.

Enrichment report hanya menambah presentasi teknikal, entry/stop/target, chart,
dan risk facts. Ia tidak melakukan scoring ulang atau mengambil artefak
multi-day legacy.

## Watchlist AI contract

Watchlist AI menerima field allow-list dari row Final Watchlist. Fakta broker
yang diizinkan adalah PRIMARY, field `today_*`, dan alignment. Field legacy
multi-day tidak masuk normalized facts maupun presentation JSON. Provider
routing dan prompt AI tidak diubah oleh arsitektur ini.

## Guardrail dan failure behavior

- Exact aggregate PRIMARY adalah authority; coverage database lokal tidak
  menentukan validitasnya.
- Summary/raw PRIMARY yang hilang, salah tanggal, atau tidak sesuai sidecar
  memblokir wrapper Final Watchlist daripada substitusi data lain.
- PRIMARY 1D tidak membuat pulse duplikat.
- TODAY raw tidak boleh diberi label PRIMARY.
- Alignment tidak boleh mengubah Broker Fusion, Decision Engine, threshold,
  entry, SL, TP, RR, atau lifecycle.

## Migrasi dan komponen deprecated

- Job CLI/integrated `broker_multi_day` telah dihapus dari graph runtime.
- Bridge yang menempelkan context multi-day ke input keputusan telah dihapus.
- Report builder dan Telegram formatter tidak lagi memiliki formatter/wrapper
  Broker Multi-Day kedua.
- Konfigurasi `broker_multiday_output_dir`, `minimum_multiday_sessions`, dan
  delivery route legacy telah dihapus dari runtime config.
- Snapshot audit quant historis masih dapat memuat
  `minimum_multiday_sessions`; validator freeze menandainya sebagai key retired
  non-quant agar baseline lama tetap dapat diaudit tanpa menghidupkan runtime.
- Dokumen audit lama dipertahankan sebagai bukti historis dan diberi banner
  superseded; dokumen ini adalah panduan operasional canonical.
