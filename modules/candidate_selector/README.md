# Technical Candidate Selector — SDE Swing V1.6

Modul memisahkan kualitas teknikal dari kesiapan entry.

Output utama:

- `Technical_Quality_Score`
- `Entry_Readiness_PreScore`
- `Entry_Readiness_Class`
- `Setup_Type`
- `Entry_Hard_Blocker`
- `Entry_Soft_Warning`

Setup type:

```text
BREAKOUT
PULLBACK
TREND_CONTINUATION
DEVELOPING
```

Filter minimum tetap mencakup validitas simbol, harga, RSI, ATR, dan turnover. Saham dengan kualitas tinggi dapat tetap kehilangan readiness jika terlalu extended, wick terlalu panjang, gap berlebihan, atau belum memiliki trigger.

Penggunaan resmi melalui `RUN_SDE.bat` atau `master_pipeline.py`.
