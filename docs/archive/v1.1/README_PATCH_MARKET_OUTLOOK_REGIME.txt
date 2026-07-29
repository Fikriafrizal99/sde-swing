SDE SWING V1.6.1 - HOTFIX MARKET OUTLOOK REGIME & FORMAT
=========================================================

CARA PASANG
1. Tutup semua CMD SDE.
2. Extract ZIP patch.
3. Copy seluruh isi hasil extract ke root SDE_SWING_V1_6_1 Active.
4. Pilih Replace/Overwrite.
5. Jalankan RUN_MARKET_OUTLOOK.bat lalu pilih mode Preview terlebih dahulu.

PERUBAHAN
- Market Outlook memperbarui IHSG secara mandiri sebelum menghitung regime.
- Jika refresh IHSG gagal, data existing dipakai dengan warning.
- Regime Market Outlook tidak lagi bergantung pada MARKET_STATUS.json Final Watchlist.
- Regime baru: STRONG BULLISH, BULLISH, EARLY BULLISH, SIDEWAYS,
  EARLY BEARISH, BEARISH, STRONG BEARISH, atau UNKNOWN.
- Perhitungan memakai posisi IHSG terhadap MA20/MA50/MA200, slope MA20/MA50,
  RSI, MACD histogram, return 5/20 hari, dan breadth saham bila tersedia.
- Struktur campuran tidak otomatis disebut SIDEWAYS.
- Telegram menampilkan tanggal data IHSG dan confidence regime.
- Warna VIX, USD/IDR, DXY, dan Gold disesuaikan dengan dampaknya terhadap risiko.
- Ringkasan global dihitung hanya dari 9 instrumen yang benar-benar ditampilkan.
- Format Market Outlook diperbarui dengan Ringkasan Global, Interpretasi,
  dan Arahan SDE.

TIDAK DIUBAH
- Technical scoring saham.
- Broker Summary dan Broker Fusion.
- Decision Engine dan threshold BUY/WATCH.
- Post Market dan Final Watchlist.
- Outcome Tracker.

OUTPUT BARU
- data/output/market_regime/<tanggal>/market_outlook_regime.json

CATATAN
Regime baru hanya digunakan untuk Market Outlook/presentation layer. Ia tidak
mengubah scoring atau keputusan saham secara langsung.
