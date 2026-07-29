# Technical Feature Engine — SDE Swing V1.2

Membaca OHLCV per simbol dari `data/output/historical/by_symbol` dan menghitung
feature teknikal tanpa TA-Lib.

Output utama:

```text
data/output/technical/latest_technical_features.csv
```

Penggunaan resmi melalui master pipeline. Standalone:

```bash
python modules/technical_feature_engine/technical_feature_engine.py \
  --input data/output/historical/by_symbol \
  --output data/output/technical
```
