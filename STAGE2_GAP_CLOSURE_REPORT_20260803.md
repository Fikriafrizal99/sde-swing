# Stage 2 Gap Closure

Implemented before Stage 3:

- permanent CI for compile, pytest, diff integrity, and redaction scan;
- official 2026 IDX/KSEI trading-holiday configuration;
- strict Top-40 Broker Summary coverage (`40/40`, 100%);
- `INSUFFICIENT_MICROSTRUCTURE_DATA` execution class;
- missing spread/depth/frequency no longer defaults to `NORMAL`;
- capital-aware participation and slippage using configured portfolio capital;
- BEAR active-position enforcement retained and blocked openings are now auditable;
- shadow metric renamed from misleading `Trigger_Rate` to `Ready_Conversion_Ratio`;
- `Actual_Trigger_Rate` is emitted only when trigger lifecycle observations exist.

Historical backtest and 20–40 live shadow sessions remain data-validation gates, not code gaps.
