# Audit SDE Swing V1.6.0 — Signal Quality & Entry Readiness

## Tujuan

Mengurangi kondisi hampir seluruh saham berhenti di WATCH tanpa memperbanyak BUY secara artifisial.

## Akar Masalah

### 1. Circular gate antara Decision dan Entry Plan

Decision Engine membutuhkan kondisi kuat untuk menghasilkan BUY, tetapi Entry Plan hanya dibuat untuk BUY/STRONG BUY. Akibatnya kandidat WATCH berkualitas tidak pernah diuji apakah sebenarnya memiliki area entry dan risk/reward valid.

### 2. Quality dan timing tercampur

Trend alignment dapat membuat skor tinggi walaupun harga sudah terlalu extended, overbought, atau jauh dari area entry.

### 3. Broker belum memiliki confidence eksplisit

Pola Acc/Dist, net flow, dan concentration dipadatkan menjadi satu label. Sinyal berlawanan dapat terlihat membingungkan dan hard gate BUY terlalu bergantung pada label tersebut.

### 4. Basis risiko pullback tidak tepat

Untuk setup pullback, current close dapat berada jauh di atas planned entry zone. Menghitung stop dan target dari current close menghasilkan risk/reward yang tidak mewakili rencana entry.

## Solusi yang Diterapkan

### Technical Quality

Menilai trend, momentum, volume, posisi harga, risiko, dan likuiditas. Trend indicators yang mirip tetap dibatasi per kelompok agar tidak menghasilkan poin tanpa batas.

### Entry Readiness

Menilai setup type, jarak EMA20, ATR extension, volume confirmation, candle close location, wick, gap, RSI, dan timing terhadap area entry.

### Broker Confidence

Flow, concentration, dan Acc/Dist menghasilkan direction, strength, confidence, dan divergence. Confidence rendah menahan status meskipun salah satu sinyal broker terlihat kuat.

### Decision Staging

```text
WATCH
-> WATCH HIGH
-> BUY CANDIDATE
-> BUY CONFIRMED
```

BUY CONFIRMED merupakan hasil gabungan keputusan kuat dan entry plan ACCEPT, bukan hanya perubahan nama dari STRONG BUY.

### Planned-entry risk model

```text
Entry Reference = sisi atas entry zone (konservatif)
Risk per Share  = Entry Reference - Initial Stop
Target 1        = Entry Reference + 1R
Target 2        = Entry Reference + 2R
```

Resistance minor dan mayor kemudian digunakan sebagai guardrail tambahan.

## Hasil Integration Test pada Snapshot Paket

Candidate readiness dari 30 kandidat:

```text
READY_ZONE : 8
DEVELOPING : 11
EARLY      : 7
NOT_READY  : 4
```

Decision Engine:

```text
STRONG BUY : 1
BUY        : 1
WATCH HIGH : 2
WATCH      : 6
SPECULATIVE: 7
AVOID      : 13
```

Entry Plan setelah guardrail planned-entry risk:

```text
ACCEPT      : 0
CONDITIONAL : 3
REJECT      : 1
```

Hasil ini menunjukkan mesin tidak memaksakan BUY CONFIRMED. Dua keputusan kuat tetap ditampilkan sebagai BUY CANDIDATE ketika trigger/resistance belum valid.

## Interpretasi

V1.6 tidak menjamin setiap hari akan menghasilkan BUY. Perbaikannya adalah:

- kandidat yang dekat BUY menjadi terlihat;
- alasan tertahannya sinyal menjadi spesifik;
- saham extended tidak bercampur dengan saham siap entry;
- BUY CONFIRMED hanya muncul ketika level risiko benar-benar lolos;
- frekuensi BUY dapat naik secara alami ketika quality, broker confidence, dan trigger entry bertemu.

## Validasi Teknis

- 79 tests: PASS.
- Python compileall: PASS.
- Candidate → Broker Fusion → Decision → Exit Plan: PASS.
- Telegram preview final: PASS.
- Tidak ada live send karena credential Telegram tidak tersedia di paket.

## Peningkatan Berikutnya yang Belum Diterapkan

- Broker flow 3 hari dan 5 hari.
- Konsistensi Top Buyer/Top Seller antarsesi.
- Broker average cost dan distance to cost.
- Buy/sell ticket behaviour.
- Backtest khusus per setup type dan status BUY CANDIDATE.

Peningkatan tersebut membutuhkan histori broker per tanggal dan tidak boleh dibangun dari satu snapshot latest.
