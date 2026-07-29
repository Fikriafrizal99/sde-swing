# Changelog — SDE Swing V1.6.0 Signal Quality & Entry Readiness

Pipeline ID: `SDE_SWING_V1_6_0_SIGNAL_QUALITY_ENTRY_READINESS`

## Masalah yang Diperbaiki

1. Technical Score tinggi belum membedakan saham berkualitas dengan saham yang benar-benar siap entry.
2. Exit Engine sebelumnya baru membuat plan setelah keputusan BUY, sehingga kandidat WATCH tidak pernah diuji kelayakan entry-nya.
3. Label broker dapat terlalu dipengaruhi pola Acc/Dist walaupun net flow dan concentration berlawanan.
4. Likuiditas terlalu dipengaruhi jumlah lot tetap dan dapat merugikan saham mahal yang sebenarnya aktif diperdagangkan.
5. Pullback plan menghitung risk/target dari current close, bukan planned entry, sehingga risk/reward dapat salah.
6. Status kuat dapat terlihat sebagai BUY CONFIRMED walaupun entry plan belum READY.

## Technical Engine

- Menambahkan jarak harga terhadap EMA.
- Menambahkan candle body, upper/lower wick, close location, dan bullish candle confirmation.
- Breakout memakai previous 20-day high agar candle terbaru tidak membandingkan dirinya sendiri.
- Menambahkan setup type: `BREAKOUT`, `PULLBACK`, `TREND_CONTINUATION`, `DEVELOPING`.
- Memisahkan:
  - `Technical_Quality_Score`
  - `Entry_Readiness_PreScore`
- Menambahkan extension penalty, soft warning, dan hard blocker.

## Broker Fusion

- Menambahkan `Broker_Direction_Score`.
- Menambahkan `Broker_Direction`, `Broker_Strength`, `Broker_Confidence`, dan `Broker_Divergence`.
- Flow, concentration, dan Acc/Dist dinilai sebagai sinyal terpisah.
- Divergence menurunkan confidence dan tidak lagi disembunyikan di balik satu label.

## Decision Engine

- Formula V1.6 menggunakan quality, readiness, broker, dan liquidity sebagai komponen terpisah.
- Menambahkan status `BUY CANDIDATE` dan `WATCH HIGH`.
- Distribution tidak dapat dinaikkan menjadi WATCH HIGH hanya karena skor teknikal tinggi.
- Jalur legacy tetap dipertahankan untuk fixture lama yang tidak memiliki field V1.6.

## Entry Plan dan Exit Engine

- Plan dapat dibuat untuk STRONG BUY, BUY, BUY CANDIDATE, WATCH HIGH, dan WATCH berkualitas tertentu.
- Kandidat dengan broker direction distribution tidak dibuatkan plan.
- Resistance minor dan mayor diperiksa terpisah.
- Menambahkan `Plan_Status=CONDITIONAL` dan output `CONDITIONAL_ENTRIES.csv`.
- Risk, stop, target, dan RR memakai `Entry_Reference_Price`, yaitu sisi konservatif dari planned entry zone.
- Current close tetap disimpan sebagai `Reference_Close` dan tidak lagi menjadi basis risiko pullback.
- Entry readiness final dibatasi sesuai hasil guardrail: CONDITIONAL tidak dapat tampil 100%, REJECT tidak dapat tetap tinggi.

## Telegram UI

- BUY CONFIRMED hanya muncul jika BUY/STRONG BUY memiliki plan READY.
- BUY/STRONG BUY tanpa plan READY menjadi BUY CANDIDATE.
- Conditional plan menampilkan trigger breakout atau area pullback yang harus ditunggu.
- Broker confidence ditampilkan pada watchlist.
- Token singkat seperti `Acc; Big Acc; Dist` dihapus dari kesimpulan karena informasi broker sudah tersedia pada field khusus.

## Validasi

- 79 unit dan regression tests lulus.
- Compile check seluruh Python lulus.
- Integration test memakai snapshot existing menghasilkan candidate, decision, dan plan tanpa error.
- Live Telegram tidak dilakukan karena token/chat ID tidak disertakan dalam paket audit.

## Batasan

Broker multi-day 3/5 hari belum diterapkan. Paket saat ini hanya memiliki Broker Summary latest dan belum memiliki histori broker terstruktur yang cukup untuk menghitung konsistensi antarsesi secara aman.
