# SDE Swing V1.6.2 — Stage 2 Moderate Calibration Report

**Tanggal implementasi:** 3 Agustus 2026  
**Versi:** `1.6.2-stage2`  
**Pipeline:** `SDE_SWING_V1_6_2_STAGE2_MODERATE_CALIBRATION`  
**Status:** implementasi dan regression selesai; shadow live 20–40 sesi belum terpenuhi dan auto-entry tetap nonaktif.

## 1. Ringkasan keputusan

Stage 2 menyederhanakan pipeline keputusan menjadi:

```text
Feature Engine
→ Candidate/Scoring Facts
→ Broker & Foreign Confirmation Facts
→ Satu Final Decision Engine
→ Entry Plan Validator
→ Telegram / Analytics
```

Field keputusan kanonis adalah:

```text
Decision_Status_Final
```

`Candidate Selector` tidak membuat keputusan. `Broker Fusion` tidak membuat keputusan. `Exit Engine` tidak menghitung ulang kualitas saham. Final Decision Engine menetapkan daya tarik setup; Entry Plan Validator hanya menentukan kesiapan eksekusi.

Produksi tetap memakai `MODERATE_BASELINE`. `MODERATE_BALANCED` dan `MODERATE_FLEXIBLE` hanya berjalan dalam shadow mode. Auto-entry tetap `false`.

## 2. Source integrity dan critical fixes

- Source Stage 2 dibangun dari archive branch GitHub yang persis, bukan source audit yang ter-redact.
- Pemeriksaan source aktif tidak menemukan literal `<REDACTED>`.
- Parser Stockbit, validasi tanggal, config hash/version, data quality, not-tradeable/suspend, strong broker distribution, hard extension, stop/target consistency, maximum risk, minimum RR, position sizing, dan active-trade guard tetap ketat.
- `STRONG_BROKER_DISTRIBUTION` tetap hard blocker.
- Stop loss tidak diperlebar untuk meningkatkan jumlah kandidat.

## 3. File dan fungsi yang diubah

| File | Fungsi/area utama | Perubahan |
|---|---|---|
| `config/pipeline.json` | profile/config | Menambahkan tiga profile moderat, setup-specific rules, shadow-only calibration, auto-entry disabled. |
| `swing_utils.py` | version constants | Versi Stage 2. |
| `modules/decision_engine/moderate_profiles.py` | `resolve_profile`, `resolve_setup_profile`, classifier broker/foreign/liquidity/extension | Sumber aturan moderat dan klasifikasi konteks bersama. |
| `modules/decision_engine/smart_selective_v162.py` | `smart_decision` | Satu policy final, readiness sebagai timing, broker/foreign hierarchy, BEAR/THIN conditional. |
| `modules/decision_engine/decision_engine.py` | `_normalize_input`, `main` | Menghapus percabangan final-decision paralel; semua input melewati policy tunggal. |
| `modules/entry_plan_validator/validator.py` | `finalize_entry_plan` | State transition execution-only: READY/TRIGGER/WATCH/AVOID tanpa re-score kualitas. |
| `modules/exit_engine/exit_engine.py` | `should_build_plan`, `build_entry_plan`, active trade opening | Setup-specific zone/support/resistance/RR/trigger; sizing multiplier; active validation. |
| `modules/candidate_selector/technical_candidate_selector.py` | `score_candidates` | Menambahkan setup facts, volume confirmation, extension class, dan liquidity execution class. |
| `modules/broker_fusion/broker_fusion.py` | `scoring_facts`, `fuse` | Menghapus keputusan BUY/WATCH/AVOID; hanya score, bonus, penalty, context, blocker. |
| `modules/backtesting/backtest_engine.py` | signal loading, market gate, evaluation, aggregation | Canonical statuses, no universal BEAR downgrade, expectancy R, MFE/MAE, false positive/negative, slippage, holding, setup/regime. |
| `modules/analytics/profile_shadow.py` | `run_profiles`, `comparison_metrics` | Menjalankan tiga profile terhadap input sama dan membuat perbandingan. |
| `tools/run_moderate_shadow.py` | CLI | Runner shadow profile. |
| `master_pipeline.py` | orchestration | Menjalankan exit validator dengan config yang sama dan profile shadow sesudah analytics. |
| `modules/job_runner/core.py` | scheduled final flow | Menjalankan config-aware validator dan shadow telemetry tanpa auto-entry. |
| `modules/runtime_config.py` | `validate_config` | Validasi seluruh profile, jumlah bobot, production profile, shadow list, dan larangan auto-entry. |
| `modules/telegram/professional_ui.py`, `swing_report_builder.py` | status selection | Memprioritaskan `Decision_Status_Final`. |
| `tests/test_stage2_moderate_calibration.py` | regression Stage 2 | Kasus BFIN-like, profile weights, broker/foreign, liquidity, BEAR, validator, shadow. |

## 4. Aturan lama dan aturan baru

### 4.1 Decision ownership

**Lama:** Candidate Selector, Broker Fusion, Decision Engine, dan Exit Engine memiliki cabang aturan yang dapat menghasilkan status berbeda.

**Baru:** hanya Final Decision Engine yang menilai daya tarik setup. Entry Plan Validator hanya membaca fakta eksekusi dan mengubah `BUY ON TRIGGER` menjadi `BUY READY` setelah trigger, zone, stop, resistance, RR, dan risk valid.

**Dampak:** veto berulang berkurang dan decision trace lebih mudah diaudit.

### 4.2 Entry readiness

**Lama:** readiness rendah dapat langsung menghasilkan rejection.

**Baru:** setup kuat dengan readiness rendah tetap `BUY ON TRIGGER`. Readiness rendah sendiri tidak masuk hard blocker.

```text
Strong setup + trigger confirmed  → BUY READY
Strong setup + trigger pending    → BUY ON TRIGGER
Medium setup                      → WATCH
Weak setup / hard blocker         → AVOID
```

### 4.3 Broker

```text
STRONG_ACCUMULATION → bonus besar
ACCUMULATION        → bonus
NEUTRAL             → 0 adjustment
DIVERGENCE          → soft penalty
DISTRIBUTION        → score/confidence turun
STRONG_DISTRIBUTION → hard blocker
```

Broker Fusion tidak lagi menetapkan keputusan final.

### 4.4 Foreign flow

```text
POSITIVE        → bonus
NEUTRAL         → tanpa penalty
NEGATIVE_MILD   → soft penalty
NEGATIVE_STRONG → penalty lebih kuat
NEGATIVE_STRONG + STRONG_DISTRIBUTION → blocker gabungan
```

Aggregate broker confirmation memakai structure + domestic flow; foreign dihitung terpisah sehingga foreign tidak dimasukkan dua kali ke composite score.

### 4.5 Liquidity

```text
NORMAL              → normal process
THIN_BUT_TRADEABLE  → trigger/confirmation, smaller size, spread/slippage check
VERY_POOR           → hard blocker
```

Klasifikasi membaca average value, daily value, frequency, spread, depth, reference capital, participation, dan estimated slippage. Thin hanya memengaruhi composite sekali; validator tidak mengurangi score lagi.

### 4.6 Market regime

```text
BULL     → normal
SIDEWAYS → setup confirmation
BEAR     → higher composite requirement, smaller size, trigger evidence, active-position limit
```

BEAR bukan hard blocker universal. Ia menjadi AVOID hanya ketika blocker serius lain sudah ada.

### 4.7 Setup-specific rules

Setiap setup mempunyai parameter sendiri untuk technical minimum, strong quality, trigger score, readiness, volume ratio, soft/hard extension, support lookback, resistance lookback, minimum RR, dan preferred RR.

Breakout memiliki toleransi extension lebih tinggi daripada pullback dan early accumulation.

## 5. Tiga profile eksperimen

| Profile | Technical | Readiness | Liquidity | Broker | Foreign | Rank | Mode |
|---|---:|---:|---:|---:|---:|---:|---|
| MODERATE_BASELINE | 42% | 18% | 10% | 17% | 8% | 5% | Production control |
| MODERATE_BALANCED | 45% | 15% | 10% | 19% | 6% | 5% | Shadow |
| MODERATE_FLEXIBLE | 47% | 12% | 10% | 21% | 5% | 5% | Shadow |

Raw data tidak diubah antar-profile.

## 6. Evaluasi BFIN-like false negative

Repository tidak menyimpan snapshot operasional BFIN yang dimaksud, sehingga hasil historis BFIN aktual tidak diklaim. Sebagai regression, dibuat kandidat BFIN-like dengan:

- technical quality `79`;
- readiness `18`;
- strong accumulation;
- foreign positive;
- liquidity normal;
- extension normal.

Hasil `MODERATE_BALANCED` adalah `BUY ON TRIGGER`, bukan `AVOID`. Readiness rendah tercatat sebagai entry condition, bukan hard blocker.

Output Entry Plan sekarang menyimpan:

- `Setup_Type`;
- `Trigger_Definition` dan `Trigger_Confirmed`;
- `Entry_Zone_Low/High`;
- `Price_Position_To_Entry_Zone`;
- `Volume_Confirmation_Pass` dan setup volume minimum;
- `Support_Lookback` dan support;
- `Resistance_Lookback`, minor/major resistance;
- `ATR_Extension` dan `Extension_Class`;
- `Entry_Readiness_PreScore/Final`;
- RR, risk, stop, target, decision trace.

Dengan data live BFIN nanti, penyebab false negative dapat dipisahkan antara setup classification, entry zone, trigger, support, resistance, candle/volume confirmation, extension, atau formula readiness—bukan langsung menurunkan threshold global.

## 7. Regression test

```text
python -m pytest -q
124 passed, 0 failed
```

Pemeriksaan tambahan:

```text
python -m compileall -q .     PASS
git diff --check             PASS
```

Broker Fusion fixture lengkap:

```text
30/30 simbol matched
Decision column dari Broker Fusion: 0
Final decision owner: DECISION_ENGINE
```

## 8. Shadow comparison — engineering fixture 40 kandidat

Ini adalah deterministic engineering fixture, bukan 20–40 sesi live dan bukan bukti profitabilitas pasar.

| Profile | Ready | Trigger | Watch | Avoid | Trigger rate | Win rate | Expectancy R | Avg MFE R | Avg MAE R | False + | False - |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MODERATE_BASELINE | 3 | 3 | 9 | 25 | 50.0% | 100% | 1.200 | 1.700 | -0.250 | 0 | 8 |
| MODERATE_BALANCED | 3 | 6 | 6 | 25 | 33.3% | 100% | 0.987 | 1.487 | -0.193 | 0 | 7 |
| MODERATE_FLEXIBLE | 3 | 7 | 5 | 25 | 30.0% | 100% | 0.977 | 1.477 | -0.179 | 0 | 6 |

Interpretasi fixture:

- Balanced/Flexible menangkap lebih banyak opportunity dan mengurangi false negative.
- Baseline memiliki expectancy fixture tertinggi dan jumlah trigger lebih selektif.
- Flexible belum memiliki bukti cukup untuk menjadi production profile.
- Jumlah BUY tidak dipakai sebagai satu-satunya indikator.

## 9. Backtest engine fixture

Backtest deterministik 40 sinyal menghasilkan untuk `BUY READY`:

```text
Trades                 : 3
Expectancy R           : 0.985
Average MFE R          : 1.140
Average MAE R          : -0.153
Stop rate              : 0%
Target 1 rate          : 100%
Target 2 rate          : 0%
False positive         : 0
Average slippage       : 0.107%
Average holding period : 7 sesi
```

Ini hanya membuktikan engine dan metrik bekerja. Ia bukan pengganti historical backtest menggunakan data pasar asli.

## 10. Shadow live status

Belum tersedia data untuk menyelesaikan gate berikut:

- Top 40 operasional dengan Broker Summary lengkap pada tanggal yang sama;
- 20–40 sesi live shadow;
- actual trigger rate;
- actual slippage dan bid-offer depth;
- actual setup/regime expectancy;
- hasil forward test BFIN-like.

Sistem telah menyiapkan file:

```text
PROFILE_SHADOW_DETAIL.csv
PROFILE_COMPARISON.csv
PROFILE_SHADOW_MANIFEST.json
```

Manifest selalu menyimpan:

```text
Mode: SHADOW_ONLY
Auto_Entry_Enabled: false
Live_Shadow_Validated: false
Minimum_Shadow_Sessions: 20
```

## 11. Risiko perubahan

- Balanced/Flexible dapat menaikkan jumlah kandidat trigger dan menambah beban monitoring.
- Broker/foreign soft penalty dapat mempertahankan kandidat yang sebelumnya cepat gugur; strong distribution tetap menahan risiko ekstrem.
- Thin dan BEAR conditional membutuhkan kualitas data spread/depth lebih baik agar kandidat dapat menjadi READY.
- Profile comparison tanpa outcome aktual tidak boleh digunakan untuk mengganti produksi.
- Legacy alias tetap dipertahankan untuk kompatibilitas; integrasi baru harus membaca `Decision_Status_Final`.

## 12. Rekomendasi konfigurasi

**Rekomendasi sementara:**

```text
Production            : MODERATE_BASELINE
Primary shadow challenger : MODERATE_BALANCED
Secondary research    : MODERATE_FLEXIBLE
Auto-entry            : DISABLED
```

Alasan:

- Baseline memiliki kontrol risiko dan expectancy terbaik pada fixture.
- Balanced menunjukkan pengurangan false negative dengan kenaikan opportunity yang masih moderat, sehingga menjadi kandidat utama untuk 20–40 sesi shadow.
- Flexible belum layak dipilih karena opportunity capture lebih tinggi belum disertai bukti live tentang false positive, slippage, dan drawdown.

Profile produksi baru hanya boleh dipilih setelah minimum 20 sesi, lebih baik 40 sesi, dengan prioritas:

1. expectancy R positif dan stabil;
2. drawdown/MAE terkontrol;
3. target/stop quality;
4. false positive dan false negative;
5. slippage serta kemudahan eksekusi;
6. konsistensi per setup dan market regime.

## 13. Kesimpulan

Stage 2 telah menyelesaikan penyederhanaan decision ownership dan menyediakan calibration framework moderat. Sistem sekarang mengurangi veto berulang tanpa melonggarkan blocker risiko utama. Implementasi layak memasuki shadow live, tetapi belum layak mengaktifkan auto-entry atau mengganti profile produksi berdasarkan fixture saja.
