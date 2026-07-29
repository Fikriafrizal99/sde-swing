# Audit Reference Swing V1.1

Baseline: `SDE_BASELINE_COMPLETE_V1`

Target: `SDE_SWING_BASELINE_COMPLETE_V1_1`

Audit V1.1 menyetujui implementasi bertahap untuk Swing saja. Scope Day Trade tidak disentuh.

## Temuan Utama

- Yahoo dapat menghasilkan baris harian terbaru dengan `Close` kosong.
- Technical Feature Engine membutuhkan closed candle valid dengan `Date`, `Open`, `High`, `Low`, `Close`, dan `Volume`.
- Candidate Selector dapat berjalan ulang tetapi tetap memakai tanggal efektif lama jika candle terbaru partial.
- Broker Summary yang berbeda tanggal tidak boleh otomatis mematikan mode interaktif.
- Data historis harus diarsipkan tanpa menggandakan harga yang sama untuk setiap `Run_ID`.

## Komponen Yang Dipertahankan

- Formula Technical Feature Engine.
- Formula dan ranking Technical Candidate Selector.
- Broker Fusion: `Technical_Score_Final`, `Broker_Score`, `Broker_Confirmation`, `Synergy_Bonus`, dan `Risk_Penalty`.
- Decision Engine v3.1 dan label: `STRONG BUY`, `BUY`, `WATCH`, `SPECULATIVE`, `AVOID`.
- Exit Engine: entry, stop loss, take profit, exit alert, dan risk calculation.
- Backtesting engine yang sudah ada, diperluas tanpa membuat engine paralel.

