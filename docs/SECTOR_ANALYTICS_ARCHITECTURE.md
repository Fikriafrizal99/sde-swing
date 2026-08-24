# Sector Analytics Architecture

## Scope

This document defines the presentation-only sector analytics used by SDE Swing.
The change is deliberately isolated to sector context for **Market Outlook** and
**Post Market**.

It does **not** change candidate selection, technical scoring, decision weights
or thresholds, broker/foreign calculations, entry/stop/target/RR, Final
Watchlist, lifecycle, portfolio state, Watchlist AI, News Monitor, IDX
disclosure, Telegram routing, or market-regime calculations.

## Single source of calculation logic

All sector aggregation logic lives in:

`modules/market_data/sector_analytics.py`

There are two public calculations in that module:

- `build_daily_sector_pulse()` for Post Market.
- `build_multiday_sector_rotation()` for Market Outlook.

`modules/market_data/sector_rotation.py` is only a compatibility facade for the
existing `produce_sector_rotation()` call site. It delegates to
`build_multiday_sector_rotation()` and contains no second copy of the formula.

This prevents the two reports from maintaining competing sector calculations.

## Sources

### Static sector master

Canonical local mapping:

`data/input/sector_metadata.csv`

Only these fields are consumed by sector analytics:

- `Symbol`
- `Sector`

Extra metadata columns are ignored. The mapping is treated as reference data,
not as a daily market feed.

Sector analytics does not import or call ZAPI. Existing ZAPI code elsewhere in
the repository is outside this scoped change and is not modified by this
architecture.

### Dynamic market data

All dynamic sector facts come from the already-produced closed-candle technical
snapshot. The relevant OHLCV-derived fields are:

- `Return_1D`
- `Return_5D`
- `Return_20D`
- `Turnover_Value` (or `Close * Volume` fallback inside the sector analytics
  presentation layer)

IHSG OHLCV from `data/input/IHSG.csv` is used only as the benchmark for the
multi-day relative rotation.

## Data flow

```text
LOCAL SECTOR MASTER
Symbol -> Sector
        |
        +-----------------------------+
                                      |
CURRENT TECHNICAL SNAPSHOT            |
Return_1D / Return_5D / Return_20D    |
Turnover_Value                         |
        |                              |
        +--------------+---------------+
                       |
              SECTOR ANALYTICS
                       |
              +--------+--------+
              |                 |
              v                 v
      DAILY SECTOR PULSE   MULTI-DAY ROTATION
         Post Market         Market Outlook
              |                 |
        current session      5D / 20D
        breadth             relative vs IHSG
        participation       strength/momentum
```

There is one shared input snapshot and one shared code owner, but two different
artifacts because they represent different horizons and meanings. They are not
duplicate data products.

## Post Market: Daily Sector Pulse

Purpose: answer **which sectors were strongest/weakest in the current IDX
session**.

The engine first aggregates each eligible sector using:

- median `Return_1D`
- positive breadth = fraction of valid constituents with `Return_1D > 0`
- positive-turnover participation = turnover of positive constituents divided
  by total turnover of the same sector

The ranking score is presentation-only:

```text
daily_score =
    0.50 * percentile_rank(median Return_1D)
  + 0.30 * percentile_rank(positive breadth)
  + 0.20 * percentile_rank(positive-turnover participation)
```

Turnover participation is directional within the sector. It does not reward a
sector merely because that sector is structurally larger than another sector.

Canonical artifact:

`data/output/market/DAILY_SECTOR_PULSE.json`

Telegram block:

```text
🔄 ROTASI SEKTOR HARI INI

🔥 <strong sector>  +x,xx% · Breadth xx%
🔥 <strong sector>  +x,xx% · Breadth xx%
🔥 <strong sector>  +x,xx% · Breadth xx%

🔻 <weak sector>    -x,xx% · Breadth xx%
🔻 <weak sector>    -x,xx% · Breadth xx%
🔻 <weak sector>    -x,xx% · Breadth xx%
```

The Post Market report does not consume the Market Outlook multi-day rotation
artifact anymore.

## Market Outlook: Multi-Day Relative Sector Rotation

Purpose: answer **which sectors are leading/improving/weakening/lagging versus
IHSG for a swing horizon**.

For each eligible sector, constituent 5D and 20D returns are aggregated by
median. The benchmark is calculated from aligned IHSG closes.

```text
RS_5D  = sector_median_Return_5D  - IHSG_Return_5D
RS_20D = sector_median_Return_20D - IHSG_Return_20D

relative_strength = 0.60 * RS_20D + 0.40 * RS_5D
relative_momentum = RS_5D - RS_20D / 4
```

Both strength and momentum are percentile-ranked across sectors and both ranks
are used in the quadrant classification:

- `LEADING`: strength rank >= 50% and momentum rank >= 50%
- `IMPROVING`: strength rank < 50% and momentum rank >= 50%
- `WEAKENING`: strength rank >= 50% and momentum rank < 50%
- `LAGGING`: strength rank < 50% and momentum rank < 50%

Canonical artifact:

`data/output/market/SECTOR_ROTATION.json`

Each eligible sector belongs to exactly one quadrant.

Market Outlook renders the four canonical terms directly:

```text
🔄 ROTASI SEKTOR — SWING
🟢 LEADING
🔵 IMPROVING
🟠 WEAKENING
🔴 LAGGING
```

Legacy `rotating_in`/`rotating_out` aliases may still be accepted at report
boundaries for compatibility, but they are not a second calculation model.

## Guardrails and freshness

Both sector products fail closed.

Default guardrails:

- minimum overall usable coverage: 90%
- minimum valid constituents per sector: 3
- current Post Market sector pulse requires `technical data_date == trade_date`
- multi-day rotation requires the IHSG benchmark date to align with the
  technical snapshot date
- stale data is never presented as current sector data

If these requirements are not met, the artifact is written as
`INSUFFICIENT_DATA` and the report shows an informational unavailable message
instead of silently falling back to an older session.

## Ownership boundary

Sector analytics is a reporting/context layer only. Its scores and quadrants
must never be fed back into official stock decisions unless a future change is
separately designed, reviewed, and explicitly approved.
