# Database Archive

## Scope

The SDE history database is downstream archival infrastructure.  It is not an
input that may alter Technical, Candidate, Broker, Decision, Entry/Exit,
Lifecycle, or Portfolio calculations during the current run.

The active entry point remains:

```text
config/pipeline.json
  -> paths.database_archiver
  -> modules/database/swing_history_db.py
```

Schema and general archive behavior remain baseline-owned by
`modules/database/swing_history_db_baseline.py`.  The active facade overrides
only the historical market-price archive with
`modules/database/market_price_archive_incremental.py`.

## Historical price archive contract

Historical Yahoo CSV files are mutable because a normal incremental download can
append a new closed candle and a repair run can correct an older candle.  The DB
therefore keeps two independent audit layers:

1. `archived_source_files` records each physical source-file SHA revision.
2. `market_prices_daily_revisions` records append-only row revisions.

`market_prices_daily` is only the current projection of the latest accepted row
revision.  The revision table remains protected by no-update/no-delete SQLite
triggers.

## Incremental row behavior

A whole-file SHA change no longer means every historical candle is a new row
revision.

For every changed CSV:

```text
file SHA changed
  -> read CSV completely
  -> normalize canonical Symbol + Date + OHLCV values
  -> load current DB projection for the file symbols
  -> compare canonical row hash
       unchanged row -> skip revision write
       new row       -> append revision + publish current
       corrected row -> append revision + publish corrected current
  -> record physical file SHA in archived_source_files
```

The canonical row hash intentionally excludes:

- whole-file `source_revision`;
- archive timestamps (`created_at`, `updated_at`).

It includes the market identity and values:

- symbol;
- price date;
- open/high/low/close;
- adjusted close;
- volume;
- source.

This means adding one daily candle to a 500-row CSV normally creates one DB row
revision, not 500 duplicate revisions.

## Why the whole CSV is still read

The archive deliberately does not read only the last line.  Yahoo/provider
repair can change an older candle, and such a correction must remain detectable.
Reading the changed file in full is the correctness boundary; avoiding SQLite
writes for unchanged rows is the performance optimization.

An exact previously archived file SHA is still skipped before CSV parsing.

## Progress output

The active archive reports counters such as:

```text
[DB] Market prices progress: 100/846 file,
     archived_rows=96,
     unchanged_rows=46416,
     skipped_files=4,
     changed_files=96
```

Definitions:

- `archived_rows`: genuinely new/corrected row revisions written this run;
- `unchanged_rows`: rows scanned from changed files but identical to DB current;
- `skipped_files`: exact physical file revisions already archived;
- `changed_files`: changed source files that were parsed;
- `scanned_rows`: total valid rows inspected inside changed files.

The `archive_prices()` return value is the number of newly appended row
revisions, so `[DB] Market prices archived: N rows` now represents actual DB
changes instead of every row merely scanned.

## First population / migration note

If the SQLite database truly has no historical market-price rows yet, the first
population still has to insert the complete history.  The incremental optimizer
cannot skip data that the DB has never stored.

The large speedup applies to subsequent daily runs and to databases that already
contain the historical rows but receive new whole-file SHAs after incremental
Yahoo updates.

## Safety invariants

The optimization must preserve all of these invariants:

- historical corrections create a new append-only row revision;
- unchanged historical values never create a duplicate revision solely because
  the file SHA changed;
- the current projection always points to the latest newly accepted value;
- an exact same source-file SHA remains idempotent;
- SQLite revision history remains no-update/no-delete;
- no SDE quant/scoring/decision configuration is modified by DB archival work.

Regression coverage lives in:

- `tests/test_historical_price_revisions_phase2.py`;
- `tests/test_incremental_market_price_archive.py`.
