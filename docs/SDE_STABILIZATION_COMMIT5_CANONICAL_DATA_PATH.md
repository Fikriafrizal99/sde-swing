# SDE Stabilization — Commit 5 Canonical Data Path

## Audit basis

Commit 5 owns two confirmed P1 findings from `docs/SDE_AUDIT_BASELINE.md`:

- `AF-P1-001` — the canonical data layer exists but is not the actual
  production Stage 1/2 execution boundary;
- `AF-P1-005` — records with conflicting market dates can still select a
  source-priority winner while `fail_closed=false`.

Commit 5 changes the data boundary only. It does **not** change decision
scoring, production profile, candidate thresholds, hard blockers, technical
indicator formulas, Entry/SL/TP calculations, lifecycle semantics, V2
publication, Telegram delivery behavior, or `auto_entry_enabled=false`.

## Production path confirmed before implementation

The production configuration already routes the technical stage to:

`modules/technical_feature_engine/post_market_validated_runner.py`

Both `master_pipeline.py` and the integrated job runner call the configured
`paths.technical_feature_engine` entry point.

Before Commit 5, that wrapper validated the Yahoo run manifest and current
symbol universe, but then hardlinked/copied the original provider CSV files into
a run-scoped directory. The frozen Technical Feature Engine therefore still
consumed provider-shaped rows rather than canonical `DailyBar` records.

`DataSourceManager` was present for readiness, telemetry and individual
canonical record routing, but it was not the real historical-series boundary.

## Canonical contract

Contract:

`SDE_CANONICAL_DAILY_HISTORY_V1`

Production boundary:

`DataSourceManager.route`

Legacy acquisition provider:

`HISTORICAL_PROVIDER`

Legacy adapter:

`LegacyHistoricalProviderAdapter`

Phase 2 ownership clarification: `HISTORICAL_PROVIDER` is the primary
`DailyBar` owner. "Legacy" describes the compatibility/acquisition adapter at
the canonical boundary; it does not mean that the provider is secondary and it
does not alter the executable resolution chain.

### Acquisition remains legacy-compatible

The Yahoo/historical downloader remains responsible for acquisition and cache
maintenance. Commit 5 deliberately does not replace the network provider or
change its refresh policy.

The provider output may no longer flow directly into the Technical Feature
Engine.

### Canonical materialization

`modules/data_sources/legacy_daily_bar_adapter.py` maps each accepted historical
row into a canonical `DailyBar` carrying:

- normalized symbol and market date;
- timezone-aware event/received timestamps;
- source provenance;
- source record ID;
- raw payload hash;
- canonical OHLCV fields;
- adjusted close when present;
- closed-candle state;
- per-field provenance.

Each record is then routed through the existing `DataSourceManager.route()`
path. That invokes the existing source router, data-quality engine and conflict
resolver.

Only accepted canonical records are written to:

`data/output/historical/canonical_runs/<run_id>/`

The frozen Technical Feature Engine is invoked against that run-scoped
canonical directory. It never receives the raw legacy provider directory.

### Quant preservation

Commit 5 does not recalculate OHLCV.

The canonical materializer writes the values carried by accepted canonical
fields. The Technical Feature Engine itself remains the Commit-1 protected
blob. Therefore indicator formulas and all downstream quant calculations remain
owned by their frozen implementation.

Rows beyond the Yahoo manifest's expected last closed date are not exposed to
the feature engine. A symbol whose latest accepted canonical bar is not exactly
the expected closed date is excluded rather than substituted with an older
session.

### Compatibility behavior retained

`TECHNICAL_INPUT_FILTER_<run_id>.json` remains present for operational
compatibility.

Its legacy `Linked_Current_Symbol_Count` field is retained, but now counts
canonical materialized symbol files. `Link_Mode_Counts` explicitly reports zero
hardlinks/copies and the canonical materialized count.

## Lineage evidence

Every canonical run writes:

`CANONICAL_DAILY_HISTORY_<run_id>.json`

The manifest records:

- canonical contract and boundary;
- source and canonical directories;
- expected closed date;
- selected/accepted/rejected symbols;
- canonical coverage;
- blocked rows after expected date;
- duplicate dates removed using the previous Technical Engine keep-last
  compatibility rule;
- row counts;
- DataSourceManager provider metadata;
- per-symbol source path/hash;
- per-symbol canonical path/hash;
- latest canonical market date.

After the frozen Technical Feature Engine succeeds, the wrapper augments
`TECHNICAL_MANIFEST_<run_id>.json` with the canonical contract, canonical
manifest path, canonical input directory, raw provider directory and the
explicit assertion:

`Engine_Input_Is_Raw_Provider_Directory = false`

This makes source -> canonical history -> technical feature lineage
reconstructable during Commit 6 re-audit.

## Market-date conflict fail closed

The canonical `ConflictResolver` no longer chooses any winner when candidate
records represent different market dates.

New behavior:

- `record = None`
- `conflict_status = CONFLICT_FAIL_CLOSED`
- `fail_closed = true`
- every conflicting candidate is marked `QUALITY_REJECTED`
- every conflicting candidate carries `SOURCE_CONFLICT`
- reason contains `MARKET_DATE_MISMATCH_FAIL_CLOSED`

No source priority, tolerance or consensus rule is allowed to choose between
different trading sessions.

Same-date field conflicts retain the existing resolver behavior.

## Regression coverage

Commit 5 adds/updates:

- `tests/test_multisource_conflict.py`
- `tests/test_canonical_data_path_commit5.py`

Targeted coverage includes:

- market-date mismatch returns no winner and fails closed;
- conflicting candidates propagate rejected quality;
- canonical materialization is run-scoped and not the raw provider folder;
- OHLCV values remain numerically unchanged through the adapter;
- rows later than the expected closed session cannot reach the engine;
- a stale symbol cannot substitute an older session for the expected date;
- the production wrapper constructs a `DataSourceManager` boundary;
- pipeline configuration still routes the technical stage through the wrapper.

Commit 6 still owns full-suite and release evidence. Existence of targeted tests
is not a claim that the entire repository is green.

## New findings tracking

In-scope implementation observations are recorded in
`docs/SDE_STABILIZATION_AUDIT_TRACEABILITY.md`.

Non-blocking observations discovered while implementing the canonical boundary
are recorded in `docs/SDE_STABILIZATION_DEFERRED_FINDINGS.md` for the
post-stabilization consolidated discussion, per the agreed audit workflow.

## Guardrails

Deliberately unchanged:

- `MODERATE_BASELINE`;
- scoring weights and thresholds;
- hard blockers;
- candidate scoring;
- protected Technical Feature Engine source;
- broker-fusion math;
- Entry / initial SL / TP1 / TP2 price calculations;
- risk/RR settings;
- lifecycle semantics from Commit 3;
- runtime/status contract from Commit 4;
- V2 artifact publication from Commit 2;
- `auto_entry_enabled=false`.

## Acceptance

Commit 5 is `IMPLEMENTED / PENDING RE-AUDIT` when:

- production technical execution consumes a run-scoped canonical materialization
  produced through `DataSourceManager`;
- raw legacy provider CSVs are not used as the Technical Feature Engine input;
- canonical input lineage includes source/canonical hashes and run ID;
- latest canonical bar for every admitted symbol matches the expected closed
  market date;
- different market-date candidates fail closed with no winner;
- protected quant files remain byte-identical;
- targeted regressions exist.

`AF-P1-001` and `AF-P1-005` become `CLOSED BY RE-AUDIT` only after Commit 6.
