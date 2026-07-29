# Database Schema Swing

Lokasi:

```text
data/database/sde_swing_history.db
```

Database digunakan sebagai archive, audit trail, dan analytics source. CSV tetap menjadi output operasional.

## Tabel

- `pipeline_runs`
- `provider_runs`
- `market_prices_daily`
- `run_data_snapshots`
- `technical_features`
- `candidates`
- `broker_snapshots`
- `broker_raw`
- `broker_summary`
- `broker_status`
- `fusion_results`
- `decision_results`
- `entry_exit_results`
- `watchlist_history`
- `watchlist_outcomes`
- `telegram_logs`

## Key Dedup Harga

```text
market_prices_daily PRIMARY KEY (symbol, price_date, source)
```

Run lineage disimpan di `run_data_snapshots`, sehingga data harga tidak perlu digandakan per run.

