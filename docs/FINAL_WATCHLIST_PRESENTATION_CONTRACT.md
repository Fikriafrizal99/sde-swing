# Final Watchlist Presentation Contract

Status: **LOCKED presentation contract**

This document covers Telegram/preview presentation only. It does not change or
own Discovery, Technical Engine, Broker Fusion, Final Decision, Exit Engine,
score formulas, thresholds, or trade-plan calculations.

## Bundle contract

Every normal Final Watchlist delivery and every approved Preview/Resend snapshot
uses this logical order:

1. exactly one `final_watchlist_summary`;
2. zero to ten `final_watchlist_detail` chart-cards;
3. exactly one `final_watchlist_csv`.

The detail cards are only for actionable decisions:

- `BUY`, `BUY READY`, `BUY CONFIRMED`;
- `BUY CANDIDATE`, `BUY ON TRIGGER`.

`WATCH`, `WAIT`, and `AVOID` remain visible in the summary/CSV but never consume
a detail chart-card slot.

The maximum detail-card count is **10**. The ranking/order remains the existing
Final Watchlist presentation order; this presentation cap never recalculates a
Final Score.

## Detail card contract

Each actionable detail is a Telegram photo/chart with one compact caption in the
operator-approved structure:

```text
📈 BIPI | BUY ON TRIGGER | 76%
PULLBACK • 21 Aug 2026

💰 152 | Entry 147–152
🛑 142 | 🎯 163 / 164 | RR 1:1,08

📊 Bullish | WAIT TRIGGER
S 119 | R 158

🏦 INSUFFICIENT DATA 0/100
Net +Rp57,81B | B/S 1/0
Cost 156 (-2,34%)

🟢 XL 43,3B • ZP 8,65B • PD 6,52B
🔴 LG 7,86B • GR 5,22B • II 2,8B

⚠️ Tunggu break >158. Jangan chase.
```

Missing or unavailable facts must remain `N/A`/the existing engine-owned status;
presentation must never invent broker days, prices, triggers, scores, or levels.
An explicit engine trigger has priority over a deterministic presentation
fallback.

A detail card without a generated chart is not a valid Preview snapshot. Preview
fails closed instead of silently approving a text-only substitute.

## Menu contract

`RUN_FINAL_WATCHLIST.bat`:

- `[1]` runs the normal Final Watchlist and sends the normal bundle to Telegram;
- `[2]` renders/checks the last completed trading result using the current locked
  presentation, without rerunning the trading engine and without sending
  Telegram;
- `[3]` sends only the hash-locked presentation snapshot most recently approved
  by `[2]` for the same trading date;
- `[4]` displays status.

Preview `[2]` may render charts and presentation artifacts, but must not rerun
Final Decision, Broker Fusion, scoring, trade-plan calculations, or AI
interpretation. Resend `[3]` does not invoke the formatter again.

## Snapshot integrity

Preview freezes:

- exact Telegram text/caption parts;
- chart attachments;
- CSV attachment;
- Telegram thread routing;
- preview manifest SHA-256;
- per-file SHA-256 values.

Resend validates those hashes before the first outbound Telegram request.
Confirmed sent sequences are checkpointed. A `ReadTimeout` after an outbound
request is treated as `DELIVERY_STATE_UNCERTAIN` and is not automatically sent a
second time because Telegram may already have accepted it.

This makes Preview and Resend a single presentation contract: **what the operator
checks in `[2]` is what `[3]` sends**.
