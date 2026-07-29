# Analytics Swing Guide

Backtesting engine lama diperluas, bukan diganti.

## Output

```text
data/output/analytics/<RUN_ID>/BACKTEST_DETAIL.csv
data/output/analytics/<RUN_ID>/BACKTEST_SUMMARY.csv
data/output/analytics/<RUN_ID>/MARKET_REGIME_SUMMARY.csv
data/output/analytics/<RUN_ID>/SUPPRESSED_REPEAT_SIGNALS.csv
data/output/analytics/<RUN_ID>/WATCHLIST_OUTCOMES.csv
```

## Horizon

Default:

```text
1,3,5,7,10,20 trading days
```

Metric lama seperti Return 5D/10D/20D, MFE, MAE, Market Regime, repeated signal suppression, dan win rate tetap dipertahankan.

## Watchlist Outcome

`WATCHLIST_OUTCOMES.csv` menambahkan:

- close D1/D3/D5/D7
- return D1/D3/D5/D7
- max/min price D7
- MFE/MAE D7
- TP1/TP2/SL hit
- final outcome

