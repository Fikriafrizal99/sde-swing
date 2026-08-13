# SDE Audit Baseline

Dokumen ini adalah landasan resmi untuk audit arsitektur, quant, runtime,
data, storage, scheduler, reporting, security, dan QA pada Stock Decision
Engine (SDE) Swing.

Dokumen ini bukan pengganti source code, konfigurasi, manifest, atau database.
Dokumen ini menjelaskan bagaimana sistem seharusnya dipahami, artefak apa
yang menjadi bukti, dan prosedur minimum yang harus diulang pada audit
berikutnya.

> **Penting:** bagian yang diberi label `BASELINE OBSERVED` adalah hasil
> observasi pada snapshot audit awal dan harus diverifikasi ulang setelah ada
> perubahan code, konfigurasi, dependency, scheduler, atau data.

---

## 1. Metadata Baseline

| Item | Nilai baseline |
|---|---|
| Nama sistem | SDE Swing |
| Paket/pipeline | `1.7.0-multisource` |
| Production profile | `MODERATE_BASELINE` |
| Auto-entry | `false` — wajib tetap nonaktif sampai release gate terpenuhi |
| Timezone operasional | `Asia/Jakarta` / WIB |
| Entry point resmi | `run_sde_job.py` |
| Integrated launcher | `run_sde_job_integrated.py` |
| Konfigurasi utama | `config/pipeline.json` |
| Konfigurasi data source | `config/data_sources.json` |
| Konfigurasi scheduler | `config/scheduler.json` |
| Kalender bursa | `config/trading_calendar.json` |
| Snapshot audit awal | 13 Agustus 2026 |
| Commit referensi audit awal | `ea93be9` |
| Mode operasional yang disetujui | supervised/shadow analysis |
| Mode yang belum disetujui | autonomous execution |

### Status dokumen

Dokumen ini harus diperbarui jika salah satu hal berikut berubah:

- entry point, dependency graph, atau urutan job;
- ownership provider atau canonical record contract;
- formula, bobot, threshold, blocker, exit, atau outcome definition;
- nama/path artefak yang menjadi source of truth;
- schema database atau aturan lineage;
- scheduler, lock, retry, fallback, atau delivery behavior;
- security permission, credential flow, atau CI workflow.

Setiap perubahan wajib menambahkan tanggal, commit, alasan perubahan, migrasi,
dan bukti test pada bagian [Audit Change Log](#15-audit-change-log).

---

## 2. Tujuan, Batasan, dan Bahasa Audit

### Tujuan sistem

SDE adalah pipeline analisis saham swing yang:

1. mengambil dan memvalidasi data pasar;
2. membangun technical features dan kandidat;
3. menggabungkan broker/foreign-flow context;
4. menghasilkan keputusan terstruktur;
5. memvalidasi entry plan dan exit plan;
6. menyimpan lineage, outcome, dan event lifecycle;
7. menyajikan hasil melalui preview, CSV, SQLite, dan Telegram.

SDE bukan broker execution system. Output `BUY READY` atau `BUY ON TRIGGER`
adalah rekomendasi/entry readiness, bukan order yang otomatis dikirim ke bursa.

### Istilah evidentiary

- **CONFIRMED ISSUE** — perilaku dapat dibuktikan dari source, konfigurasi,
  test, log, manifest, atau runtime artifact.
- **POTENTIAL RISK** — kondisi berbahaya dapat diturunkan dari desain, tetapi
  belum terbukti menghasilkan kerusakan pada snapshot runtime yang diaudit.
- **BASELINE OBSERVED** — fakta pada snapshot audit tertentu; bukan janji bahwa
  fakta tersebut tetap benar selamanya.
- **SOURCE OF TRUTH** — artefak yang berwenang untuk menjawab suatu pertanyaan.
- **DERIVED ARTIFACT** — output yang dihitung dari source of truth dan tidak
  boleh dipakai untuk menggantikan input asalnya.

### Severity

| Level | Arti |
|---|---|
| P0 | Release blocker atau risiko integritas keputusan/data yang serius |
| P1 | Harus diperbaiki sebelum production hardening/autonomous execution |
| P2 | Risiko operasional/maintainability penting, tetapi ada workaround |
| P3 | Improvement, dokumentasi, atau isu presentasi non-kritis |

---

## 3. Arsitektur Sistem

### 3.1 Logical architecture

```text
Launcher / Scheduler
        |
        v
Integrated Job Runner
        |
        v
RunnerContext / RuntimeContext
        |
        +--> Runtime Config + Calendar + Dependency Validation
        |
        +--> DataSourceManager / SourceRouter
        |       +--> Canonical records
        |       +--> Quality checks
        |       +--> Conflict resolution
        |       +--> Provider health/provenance
        |
        +--> Historical/Yahoo refresh
        +--> Technical Feature Engine
        +--> Candidate Selector
        +--> Technical Snapshot Builder
        +--> Broker Summary / Broker Fusion
        +--> Broker Multi-Day Context Bridge
        +--> Decision Engine
        +--> Exchange Status Filter
        +--> Exit Engine / Entry Plan Validator
        +--> Shadow Profile / Outcome Tracker
        +--> SQLite Archive
        +--> Report Builder / AI Presentation Layer
        +--> Telegram Router / Delivery
        +--> Status + Logs + Manifests
```

Dokumentasi arsitektur berada di [ARCHITECTURE.md](ARCHITECTURE.md), tetapi
auditor wajib membandingkan dokumentasi dengan execution path aktual. Jalur
yang didokumentasikan tidak otomatis berarti jalur yang benar-benar dipakai.

### 3.2 Ownership matrix

| Domain | Owner utama | Artefak/bukti |
|---|---|---|
| Orchestration | `run_sde_job.py`, `run_sde_job_integrated.py` | job status, logs |
| Runtime context | `modules/job_runner/runtime.py` | config audit, status payload |
| Provider readiness | `modules/runtime/data_source_manager.py` | provider metadata |
| Routing/conflict | `modules/data_sources/router.py`, `conflict_resolver.py` | canonical resolution |
| Historical OHLCV | historical downloader | per-symbol history, refresh manifest |
| Technical features | `modules/technical_feature_engine/technical_feature_engine.py` | technical CSV, manifest |
| Candidate ranking | `modules/candidate_selector/technical_candidate_selector.py` | top-N candidate CSV |
| Broker normalization | `modules/broker_bridge/broker_raw.py` | canonical broker input |
| Broker fusion | `modules/broker_fusion/broker_fusion.py` | `FINAL_DECISION_V2.csv` |
| Broker multi-day | `modules/job_runner/core.py` | multi-day summary/detail/context |
| Final decision | `modules/decision_engine/smart_selective_v162.py` | `FINAL_DECISION_V3.csv` |
| Entry readiness | `modules/entry_plan_validator/validator.py` | entry plan status |
| Exit | `modules/exit_engine/exit_engine.py` | exit alerts/state/manifest |
| Lifecycle/outcome | `modules/analytics/outcome_tracker.py` | lifecycle events/outcome ledger |
| Historical archive | `modules/database/swing_history_db.py` | SQLite tables |
| Reporting | `modules/job_runner/reports.py`, enhanced report modules | preview payloads |
| Telegram routing | `modules/telegram/router.py` | topic/category route |
| Telegram delivery | `modules/job_runner/delivery.py` | delivery log, message IDs |
| AI narrative | `modules/ai_interpretation/groq_interpreter.py` | presentation-only text |

### 3.3 Designed architecture versus observed architecture

Audit harus selalu memiliki dua diagram:

1. **Designed architecture** — berdasarkan docs/config/contract.
2. **Observed execution architecture** — berdasarkan subprocess, import,
   file write, runtime log, manifest, dan database evidence.

Pada baseline awal, `DataSourceManager` sudah tersedia sebagai facade canonical,
tetapi Stage 1/2 masih menjalankan legacy historical/technical subprocess secara
langsung. Ini harus dianggap sebagai architectural gap sampai terbukti sudah
diganti.

---

## 4. Model Data dan Provenance

### 4.1 Canonical record types

Contract canonical baseline memiliki sembilan record type:

```text
DailyBar
IntradayQuote
OrderBookSnapshot
BrokerFlow
ForeignFlow
TradingStatus
CorporateAction
MarketIndex
SymbolMetadata
```

Setiap record canonical idealnya memiliki envelope minimal:

```text
record_type
symbol
market_date
event_timestamp
source
source_record_id
is_closed
quality_status
fallback_used
raw_reference
```

Auditor harus memeriksa bahwa `market_date`, `event_timestamp`, dan `source`
tidak hilang hanya karena data dibaca dari file fallback.

### 4.2 Source ownership baseline

| Record type | Primary baseline | Fallback/aturan |
|---|---|---|
| DailyBar | `HISTORICAL_PROVIDER` | file/historical fallback, closed candle only |
| IntradayQuote | internal/provider adapter | tidak menjadi input daily decision tanpa validasi |
| OrderBookSnapshot | internal/provider adapter | tidak boleh diisi angka dummy |
| BrokerFlow | `STOCKBIT` atau file capture | coverage/date wajib diverifikasi |
| ForeignFlow | derived dari broker source | gross denominator dan provenance wajib ada |
| TradingStatus | `ZAPI_IDX`/exchange status | fail closed bila ambiguous |
| CorporateAction | configured adapter | unsupported harus eksplisit |
| MarketIndex | historical provider | stale/missing harus terlihat |
| SymbolMetadata | `ZAPI_IDX`/metadata file | bukan pengganti trading status |

### 4.3 Data quality vocabulary

Status yang harus dibedakan:

- `VALID`
- `VALID_WITH_ZAPI_WARNING`
- `PARTIAL_COVERAGE`
- `WAITING_DATA`
- `NOT_CONFIGURED`
- `INVALID`
- `FAILED`

`VALID_WITH_ZAPI_WARNING` berarti jalur utama masih usable dengan warning,
bukan berarti semua provider sehat.

`PARTIAL_COVERAGE` tidak boleh disamakan dengan `VALID` tanpa policy eksplisit.

### 4.4 Artifact contract

| Tahap | Artifact baseline | Kunci validasi |
|---|---|---|
| Historical | `data/output/historical/by_symbol/*.csv` | symbol, closed date, OHLCV |
| Technical | `latest_technical_features.csv` | technical date, engine version, quality |
| Candidate | `technical_candidates_top40.csv` | candidate date, top-N, min score |
| Snapshot | `data/output/snapshots/<date>/...` | snapshot ID, config hash, source hash |
| Broker input | `BROKER_SUMMARY_LATEST.csv`, raw capture | broker date, period, coverage |
| Fusion | `data/input/FINAL_DECISION_V2.csv` | technical hash, broker hash, run ID |
| Decision | `FINAL_DECISION_V3.csv` | input hash, profile, status counts |
| Entry/exit | `data/output/exit/` | decision hash, RR, stop, target, state |
| Final run | `SWING_RUN_MANIFEST_<run>.json` | dates, coverage, quality, output links |
| Delivery | `delivery_log.jsonl` | idempotency key, message ID, route |
| Archive | `sde_swing_history.db` | run ID, dataset hash, outcome |

Path saja tidak cukup sebagai lineage. Auditor harus meminta hash, run ID,
config hash, source date, dan parent/dependency run ID.

---

## 5. Runtime dan Cara Kerja

### 5.1 Scheduled jobs

| Job | Fungsi | Output utama |
|---|---|---|
| `pre_market` | readiness/preparation | status/pre-market artifact |
| `market_outlook` | global market/regime/sector context | global snapshot, regime |
| `post_market` | technical scan tanpa final broker decision | historical, technical, candidates, snapshot |
| `technical_snapshot` | expose current snapshot | snapshot status |
| `universe_selection` | validate universe | universe status |
| `candidate_selection` | rank/filter candidates | candidate status |
| `broker_summary` | import/validate broker capture, fusion | broker summary, fusion |
| `broker_multi_day` | build period context | multi-day outputs/context |
| `final_watchlist` | final decision, exit plan, report | V3, entry/exit, Telegram |
| `final_decision` | expose/canonicalize decision artifact | decision status |
| `telegram_delivery` | delivery-only operation | delivery status/log |
| `job_status` | status inspection | status report |

### 5.2 Final decision flow

```text
Technical Snapshot VALID
        |
        v
Broker Summary ready + date/coverage valid
        |
        v
Broker Fusion -> FINAL_DECISION_V2.csv
        |
        v
Multi-day context bridge
        |
        v
Decision Engine -> FINAL_DECISION_V3.csv
        |
        v
Exchange status application
        |
        v
Exit Engine -> ENTRY_PLANS / alerts / active state
        |
        +--> Shadow profiles
        +--> Outcome tracker
        +--> SQLite archive
        +--> Reports / Telegram
```

### 5.3 Full manual baseline

Full manual official flow saat baseline:

```text
global_market_preparation
post_market
technical_snapshot
universe_selection
candidate_selection
market_outlook
broker_summary
broker_multi_day
final_watchlist
```

Urutan ini harus diaudit ulang jika full manual diklaim setara dengan jadwal
harian, karena market outlook secara operasional biasanya terjadi sebelum
post-market.

### 5.4 Locking model

Lock yang harus dipahami auditor:

- per-job lock, misalnya `post_market.lock`;
- global resource lock, misalnya `sde_pipeline_write.lock`;
- delivery idempotency index, bukan pengganti process lock;
- SQLite transaction/WAL lock, bukan pengganti file artifact lock.

Audit wajib mencatat job mana yang memegang lock mana. Jangan menyimpulkan
bahwa semua writer aman hanya karena satu job memiliki global lock.

---

## 6. Model Quant dan Decision Policy

### 6.1 Production profile

Profile production baseline: `MODERATE_BASELINE`.

Bobot baseline:

```text
technical_quality      0.42
entry_readiness        0.18
liquidity              0.10
broker_decorrelated    0.17
foreign                0.08
relative_rank          0.05
Total                  1.00
```

Bobot, threshold, profile, dan hard blocker harus dibaca dari
`config/pipeline.json`, bukan ditebak dari output CSV.

### 6.2 Decision statuses

```text
BUY READY
BUY ON TRIGGER
WATCH
AVOID
```

Interpretasi:

- `BUY READY` — entry plan lolos dan trigger/guardrail terpenuhi;
- `BUY ON TRIGGER` — setup menarik tetapi menunggu trigger/konfirmasi;
- `WATCH` — belum cukup kuat untuk entry, tetapi masih dipantau;
- `AVOID` — blocker atau score/quality tidak memenuhi.

### 6.3 Hard blockers

Baseline hard blocker meliputi:

- invalid/failed data;
- suspended/not tradeable;
- entry hard blocker;
- strong broker distribution;
- hard price extension;
- very poor liquidity;
- invalid stop;
- risk/reward di bawah minimum;
- tidak ada resistance path yang valid.

`Data_Quality_Status` harus ditelusuri dari source, bukan hanya dipercaya dari
kolom output final.

### 6.4 Entry dan exit policy

Baseline exit policy:

- stop menggunakan support/ATR/risk cap;
- target 1 dan target 2 mengikuti risk/reward/resistance;
- setelah `+1R`, stop dapat bergerak ke breakeven;
- setelah `+1.5R`, trailing mengikuti EMA/ATR;
- TP1 pada exit engine mengaktifkan trailing, bukan otomatis menutup seluruh
  posisi;
- stop diprioritaskan bila stop dan target tersentuh pada candle yang sama.

### 6.5 Outcome contract yang wajib disatukan

Audit masa depan harus memastikan definisi berikut identik pada:

```text
exit_engine.py
outcome_tracker.py
swing_history_db.py
profile shadow runner
backtest/evaluation scripts
```

Minimal yang harus sama:

- TP1: partial/trailing atau full close;
- TP2: full close;
- stop dan target pada same candle;
- max-hold exit;
- actual entry versus reference close;
- outcome `WIN`, `LOSS`, `AMBIGUOUS`, `OPEN`;
- window evaluasi dan trading-session calendar.

Perbedaan definisi di antara modul adalah risiko langsung terhadap win rate,
expectancy, MFE/MAE, dan calibration report.

---

## 7. Storage dan Database Model

Database utama: `data/database/sde_swing_history.db`.

Database broker period: `data/database/broker_multiday.db`.

Tabel penting pada database utama:

```text
pipeline_runs
provider_runs
market_prices_daily
run_data_snapshots
technical_features
candidates
broker_snapshots
broker_raw
broker_summary
broker_status
fusion_results
decision_results
entry_exit_results
watchlist_outcomes
lifecycle_events
portfolio_positions
telegram_logs
archived_source_files
```

### 7.1 Database audit rules

Auditor harus memeriksa:

1. `PRAGMA integrity_check` bernilai `ok`;
2. setiap `pipeline_run` memiliki manifest yang dapat dibaca;
3. dataset hash sesuai dengan file yang diarsipkan;
4. source revision tidak menghapus history penting;
5. primary key mencegah duplicate yang tidak disengaja;
6. outcome row memiliki quality status;
7. lifecycle event memiliki event ID idempotent;
8. Telegram log memiliki run ID dan delivery status.

### 7.2 Shared file warning

`FINAL_DECISION_V2.csv`, `BROKER_SUMMARY_LATEST.csv`, raw latest, technical
latest, status latest, dan delivery index adalah mutable convenience artifacts.

Artifact tersebut tidak boleh menjadi satu-satunya bukti audit. Selalu cari:

- run-scoped manifest;
- dated artifact;
- source hash;
- output hash;
- config hash;
- timestamp;
- parent/dependency run ID.

---

## 8. Reporting, AI, dan Telegram

### 8.1 Reporting contract

Report harus menjadi presentation layer. Report tidak boleh:

- menghitung ulang keputusan;
- mengubah score/status/protected decision columns;
- mengisi angka yang tidak ada dengan angka fiktif;
- mencampur artifact tanggal berbeda;
- menyatakan broker context valid bila coverage atau period invalid.

### 8.2 AI contract

AI interpreter hanya untuk narasi/presentation. Field immutable yang harus
dilindungi:

```text
decision status
score
entry
stop
target
risk/reward
broker evidence
data quality
source lineage
```

Fallback deterministic harus tersedia ketika provider AI tidak configured,
timeout, malformed response, atau melebihi call cap.

### 8.3 Telegram contract

Audit harus memeriksa:

- category dan topic ID;
- HTML escaping dynamic values;
- maximum message length dan split integrity;
- idempotency key;
- force resend behavior;
- partial delivery dan failed payload;
- lifecycle notification acknowledgement;
- retry/backoff untuk transient error.

News harus tetap terisolasi dari main report/signal topic.

---

## 9. Observability dan Evidence Chain

### 9.1 Evidence hierarchy

Urutan bukti yang disarankan:

1. source/config commit;
2. runtime config audit;
3. run manifest;
4. snapshot manifest;
5. provider/broker manifest;
6. decision engine manifest;
7. exit manifest;
8. status dated dan log;
9. CSV output;
10. Telegram preview/delivery log;
11. SQLite archive.

Jika dua artefak bertentangan, artefak yang lebih dekat ke engine dan memiliki
run ID/hash harus didahulukan daripada `*_latest.json` atau pesan Telegram.

### 9.2 Minimum status fields

Status job minimal harus memiliki:

```text
run_id
job
status
current_stage
exit_code
trade_date
started_at
finished_at
config_version
config_hash
data_status
provider_status
data_source_mode
snapshot_id
dependency_status
output_paths
warnings
errors
traceback_path
telegram_status
lock_status
```

Status `FAILED` dengan `exit_code=0`, stage `START`, dan tanpa error/traceback
harus dianggap observability defect sampai terbukti hanya artifact stale.

### 9.3 Lineage validation

Untuk satu run final, auditor harus dapat menjawab:

```text
Run ID apa?
Technical snapshot ID apa?
Technical date apa?
Broker date/period apa?
Coverage berapa?
Input V2 hash apa?
Output V3 hash apa?
Decision profile apa?
Entry/exit membaca output yang mana?
Database archive menyimpan run yang sama atau tidak?
Telegram mengirim artifact run yang mana?
```

---

## 10. Baseline Observed — Snapshot Audit Awal

Bagian ini adalah fakta pada audit awal, bukan nilai permanen.

### 10.1 Runtime run 12 Agustus 2026

Run final yang tervalidasi:

```text
Run ID              SWING-20260812-223409-24a9
Technical date      2026-08-12
Broker date         2026-08-12
Broker coverage     40/40 - 100%
Quality             VALID_WITH_ZAPI_WARNING
ZAPI                degraded / warning
ZAPI requests       5
Decision rows       40
BUY ON TRIGGER      11
WATCH               12
AVOID               17
Auto-entry          false
```

Manifest yang menjadi evidence:

```text
data/output/manifests/SWING_RUN_MANIFEST_SWING-20260812-223409-24a9.json
data/output/manifests/BROKER_FUSION_MANIFEST_SWING-20260812-223409-24a9.json
data/output/manifests/DECISION_ENGINE_MANIFEST_SWING-20260812-223409-24a9.json
data/output/manifests/EXIT_MANIFEST_SWING-20260812-223409-24a9.json
```

### 10.2 Baseline test evidence

```text
Collected tests                 520
Passed                          492
Failed                         28
Subtests passed                  3
compileall                       PASS
runtime config validator         PASS
canonical/multi-day validator    PASS
SQLite integrity                 PASS
git diff --check                 PASS
```

README yang menyatakan `124/124 tests PASS` harus dianggap stale sampai
diperbarui berdasarkan release decision yang disepakati.

### 10.3 Baseline known issues

| Priority | Issue | Status |
|---|---|---|
| P0 | CI suite merah, 28 failure | CONFIRMED ISSUE |
| P0 | Shared V2 writer tidak seluruhnya berada di global lock | CONFIRMED ISSUE; race POTENTIAL RISK |
| P1 | Canonical layer belum menjadi production execution path | CONFIRMED ISSUE |
| P1 | TP1 semantics berbeda antar engine/tracker/DB | CONFIRMED ISSUE |
| P1 | Resend menimpa `*_latest.json` engine status | CONFIRMED ISSUE |
| P1 | Interrupt dapat meninggalkan FAILED + exit code 0 | CONFIRMED ISSUE |
| P1 | Conflict date memilih winner dengan fail-closed false | CONFIRMED ISSUE |
| P2 | Telegram idempotency check/write tanpa transaction lock | POTENTIAL RISK |
| P2 | Hotfix workflow memiliki `contents: write` dan auto-push | CONFIRMED ISSUE |
| P2 | DB revision belum sepenuhnya immutable | POTENTIAL RISK |

---

## 11. Audit Procedure untuk Audit Berikutnya

### 11.1 Persiapan

Catat sebelum pemeriksaan:

```text
tanggal dan timezone audit
branch
commit SHA
Python version
dependency lock/version
config hash
environment mode: LIVE/MOCK/FILE
credential availability tanpa mencetak secret
```

### 11.2 Repository and governance

```powershell
git status --short
git log -1 --oneline
git diff --check
rg --files
Get-Content config/pipeline.json
Get-Content config/data_sources.json
Get-Content config/scheduler.json
Get-Content config/trading_calendar.json
```

Periksa juga workflow CI dan workflow yang memiliki permission lebih tinggi
dari `contents: read`.

### 11.3 Static/runtime checks

```powershell
& '.\.venv\Scripts\python.exe' -m compileall -q modules tools run_sde_job.py run_sde_job_integrated.py
& '.\.venv\Scripts\pytest.exe' --collect-only -q
& '.\.venv\Scripts\pytest.exe' -q
& '.\.venv\Scripts\python.exe' tools/ci_validate_runtime_config.py
& '.\.venv\Scripts\python.exe' tools/ci_validate_contracts.py
```

Jangan menganggap validator contract cukup; validator harus dilengkapi dengan
execution evidence dan lineage validation.

### 11.4 Runtime evidence

Untuk run yang dipilih:

1. pilih `SWING_RUN_MANIFEST_<run_id>.json`;
2. ikuti semua path pada `Output_Files`;
3. hitung ulang SHA256 output;
4. cocokkan technical date, broker date, period, coverage;
5. cocokkan V2 input hash pada decision manifest;
6. cocokkan decision output hash pada exit manifest;
7. cek status dated versus latest;
8. cek log dari START hingga terminal event;
9. cek delivery log dan topic;
10. cek rows/quality di SQLite.

### 11.5 Database checks

Gunakan koneksi read-only bila database sedang dipakai. Minimum:

```sql
PRAGMA integrity_check;
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
SELECT run_id, status, technical_date, broker_date, data_quality_status
FROM pipeline_runs
ORDER BY COALESCE(finished_at, started_at) DESC
LIMIT 10;
```

### 11.6 Quant checks

Auditor wajib mengulang:

- bobot total = 1.0;
- profile production sesuai config;
- blocker tidak berubah diam-diam;
- `VALID_WITH_ZAPI_WARNING` diperlakukan sesuai policy;
- decision columns protected oleh bridge;
- entry plan tidak mempromosikan `BUY ON TRIGGER` menjadi `BUY READY` tanpa
  trigger/plan pass;
- TP1/TP2/SL/same-candle/max-hold konsisten lintas modul;
- shadow output tidak memengaruhi production decision.

### 11.7 Release sign-off

Release tidak boleh disetujui jika salah satu berikut benar:

- test wajib gagal tanpa waiver tertulis;
- lineage run final tidak dapat direkonstruksi;
- output shared ditulis tanpa locking/atomicity yang disetujui;
- status engine dan status resend tidak dapat dibedakan;
- outcome definition berbeda antar evaluator;
- auto-entry aktif tanpa approval dan shadow gate;
- credential/CI governance melanggar policy.

---

## 12. Guardrails yang Tidak Boleh Hilang

- `auto_entry_enabled=false`;
- closed-candle policy;
- broker date dan coverage validation;
- data quality propagation;
- exchange status / suspended / UMA handling;
- hard blocker liquidity dan broker distribution;
- protected decision columns pada context bridge;
- deterministic AI fallback;
- Telegram HTML escaping dan message splitting;
- config hash dan run manifest;
- SQLite foreign-key/WAL/integrity checks;
- lifecycle event idempotency;
- shadow-only profile comparison.

Perubahan pada guardrail harus memiliki alasan quant, test regression, dan
sign-off eksplisit.

---

## 13. Change Control Template

Salin template berikut untuk setiap perubahan arsitektur besar.

```text
Audit/change ID:
Tanggal:
Auditor/owner:
Commit awal:
Commit akhir:

Komponen yang berubah:
Jenis perubahan: code / config / data contract / scheduler / DB / UI / security

Designed architecture sebelum:
Observed architecture sebelum:
Designed architecture sesudah:
Observed architecture sesudah:

Source of truth yang berubah:
Artifact/path yang berubah:
Schema migration:
Backward compatibility:
Locking/atomicity impact:
Lineage/hash impact:
Quant/decision impact:
Outcome/backtest impact:
Telegram/report impact:
Security impact:

Test yang dijalankan:
Runtime evidence:
Database evidence:
Known residual risk:
Rollback plan:
Approval:
```

---

## 14. Audit Report Template Ringkas

```text
# SDE Audit Report — <tanggal>

Commit:
Config hash:
Package version:
Auditor:
Scope:

## Executive verdict
Health score:
Release decision: GO / CONDITIONAL / NO-GO

## Evidence
- Test result:
- Runtime run ID:
- Technical date:
- Broker date/period:
- Coverage:
- Quality:
- Decision counts:
- DB integrity:
- Telegram/delivery:

## Findings
| ID | Priority | Classification | Component | Evidence | Impact | Owner |
|---|---|---|---|---|---|---|

## Quant validation
- Weight sum:
- Blocker regression:
- Entry/exit contract:
- Outcome contract:
- Shadow comparison:

## Lineage validation
- Source:
- Snapshot:
- Fusion:
- Decision:
- Exit:
- DB archive:
- Delivery:

## Residual risks

## Approval / waiver
```

---

## 15. Audit Change Log

| Tanggal | Commit | Perubahan | Alasan | Dampak audit |
|---|---|---|---|---|
| 2026-08-13 | `ea93be9` | Baseline dibuat dari audit repository/runtime awal | Membentuk referensi audit berulang | Menetapkan architecture, lineage, quant, runtime, dan release checklist |
| 2026-08-13 | Phase 2 Commit 3 (SHA Git-authoritative) | Menambahkan kontrak replay eksplisit dan final Phase 2 re-audit | Mencegah historical performance mengklaim full live replay tanpa runtime context | Menambahkan `SDE_SWING_REPLAY_V1`; hasil historis default tetap price-lifecycle-only dan autonomous trading tetap NO-GO |

---

## 16. Final Baseline Statement

Pada baseline awal, SDE memiliki decision guardrail dan fondasi provenance yang
cukup kuat untuk supervised shadow analysis. Sistem belum boleh diperlakukan
sebagai autonomous trading engine.

Audit berikutnya harus memulai dari dokumen ini, lalu membuktikan perubahan
dengan source, config, manifest, log, database, dan test — bukan hanya dari
pesan Telegram atau `*_latest.json`.
