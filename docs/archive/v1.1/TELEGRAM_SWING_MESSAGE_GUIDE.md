# Telegram Swing Message Guide V1.5

Seluruh UI Telegram menggunakan formatter pusat:

```text
modules/telegram/professional_ui.py
```

## Full Manual Dry Run

```bat
python modules\telegram\telegram_bot.py --config config\telegram.json --dry-run swing ^
  --run-id <RUN_ID> ^
  --run-manifest data\output\manifests\SWING_RUN_MANIFEST_<RUN_ID>.json ^
  --decisions data\output\decision\FINAL_DECISION_V3.csv ^
  --entry-plans data\output\exit\ENTRY_PLANS.csv ^
  --market-status data\output\decision\MARKET_STATUS.json ^
  --exit-alerts data\output\exit\EXIT_ALERTS.csv ^
  --watchlist-outcomes data\output\analytics\<RUN_ID>\WATCHLIST_OUTCOMES.csv ^
  --backtest-summary data\output\analytics\<RUN_ID>\BACKTEST_SUMMARY.csv
```

## Scheduler Dry Run

```bat
python run_sde_job.py --job post_market --preview-existing --dry-run --trade-date 2026-07-24 --force
```

## Preview

```text
data/output/telegram_ui_preview/scheduled/
data/output/previews/<trade-date>/
data/output/telegram_preview/<RUN_ID>/
```

## Guardrail

- Keputusan internal tetap memakai nilai asli seperti `STRONG BUY`, `BUY`, `WATCH`, dan `AVOID`.
- UI memetakan status tersebut menjadi `BUY CONFIRMED`, `WATCH HIGH`, `WATCH`, dan `AVOID`.
- Entry, TP, dan SL hanya ditampilkan sebagai level valid jika entry plan lolos guardrail.
- Plan `REJECT` tampil sebagai `ENTRY READINESS: NOT READY`.
- HTML dynamic value selalu di-escape.
- Emoji dapat dimatikan untuk mode compatibility melalui `use_emoji: false`.
- Topic ID tidak di-hardcode dan tetap dibaca dari konfigurasi scheduler.
