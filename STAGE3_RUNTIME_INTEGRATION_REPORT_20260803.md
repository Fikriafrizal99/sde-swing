# Stage 3 Runtime Integration Report

**Branch:** `agent/sde-swing-v1-7-0-multisource`  
**Runtime/config version:** `1.7.0-multisource`  
**Official entry point:** `run_sde_job.py`  
**Compatibility entry point:** `master_pipeline.py` (deprecated marker; retained for Stage 1/2 callers)

## Scope and result

Stage 3 now has one shared runtime facade without recreating the application:
`RunnerContext` can expose a `RuntimeContext`, and both expose the same
`DataSourceManager`, canonical routing, source-health metadata, status writer,
snapshot metadata, and Telegram topic router. Existing Stage 1/2 engines and
their imports remain intact.

## Files added/modified

Added:

- `modules/runtime/` (`context.py`, `data_source_manager.py`, `jobs.py`,
  `status.py`, `artifacts.py`, package exports);
- `modules/decision/` canonical candidate contract;
- `modules/snapshots/` metadata/hash snapshot builder;
- `modules/telegram/router.py`;
- seven integration/Telegram tests;
- nine runtime/configuration documentation files.

Modified:

- `run_sde_job.py` job registry and dependency-aware wrappers;
- `modules/job_runner/runtime.py` unified metadata/status fields and manager
  access;
- `modules/job_runner/core.py` post-market symbol accounting and snapshot
  metadata;
- `modules/job_runner/delivery.py` three-topic routing and delivery telemetry;
- version/config files, CI validators, `.gitignore`, and compatibility marker.

Moved/deleted/archived: none. Existing files were classified before change;
legacy and archive candidates remain until an import/entry-point review gives a
safe removal decision. Runtime output and local secrets are ignored.

## Before/after tree

Before, provider readiness was spread across the Yahoo/global-market path,
broker bridge, and job-specific checks. After, the stable additions are:

```text
modules/
  runtime/       context, manager, dependency graph, status, artifacts
  data_sources/  adapters, canonical schema, quality, conflict, health
  snapshots/     metadata-first snapshot builder
  decision/      canonical candidate object
  telegram/      TelegramRouter plus existing UI
data/output/
  job_status/ snapshots/{market,technical,broker,broker_multi_day}/
  watchlists/ decisions/ previews/ reports/ audits/
```

The output directories are runtime-only and are covered by `.gitignore`; test
fixtures under `tests/fixtures/` remain visible to Git.

## Runtime flow and dependency graph

```text
Job Runner
 -> Runtime Context / path resolver
 -> DataSourceManager
 -> MultiSourceRouter
 -> Canonical Schema + Quality + Conflict Resolver
 -> Snapshot Builder
 -> Analysis / Decision adapters
 -> ReportPayloads
 -> TelegramRouter
 -> Unified StatusWriter + audit trail
```

The integrated graph is:

```text
pre_market -> market_outlook -> post_market -> technical_snapshot
technical_snapshot -> universe_selection -> candidate_selection
technical_snapshot -> broker_summary -> broker_multi_day
market_outlook + post_market + broker_summary + broker_multi_day
  -> final_watchlist -> final_decision -> telegram_delivery
```

Final Watchlist validates predecessor status, `trade_date`, and
`config_version`; it does not fetch providers itself or consume an unvalidated
`latest` artifact.

## Source mapping per job

| Job | Canonical/source path | Metadata |
|---|---|---|
| pre-market / market outlook | `MarketIndex` plus existing global snapshot | provider, mode, coverage, health, snapshot ID |
| post-market / technical | `DailyBar` and historical file fallback | requested/loaded/valid/failed/skipped and content hash |
| broker summary | `BrokerFlow`/`ForeignFlow` through Stockbit adapter | API, CSV, JSON/Tampermonkey/local raw readiness |
| broker multi-day | canonical broker history and 1D/3D/5D/10D/20D engine | date range, missing day, source, coverage |
| final watchlist | dependency artifacts only | dependency status and snapshot IDs |
| final decision | `CanonicalCandidate` | source provenance and snapshot IDs required |

## Source readiness

ZAPI is `NOT_CONFIGURED` by default. It becomes `LIVE` only if enabled,
documentation is configured, base URL exists, and API key exists. Forced/offline
runs are `MOCK` and never `LIVE`. Stockbit API may be empty; in that case the
manager keeps the API `NOT_CONFIGURED` and uses an available file export as
`FILE`/fallback. Provider metadata is never blank for data jobs.

## Unified status example: post_market

```json
{
  "run_id": "SDE-POST-MARKET-...",
  "job_name": "post_market",
  "status": "SUCCESS_WITH_WARNING",
  "current_stage": "POST_MARKET",
  "trade_date": "2026-08-03",
  "config_version": "1.7.0-multisource",
  "data_status": "VALID",
  "data_source_mode": "FILE",
  "provider_status": "FALLBACK",
  "symbols_requested": 40,
  "symbols_loaded": 38,
  "symbols_valid": 37,
  "symbols_failed": 1,
  "symbols_skipped": 2,
  "source_coverage_ratio": 0.925,
  "snapshot_ids": {"technical": "SWING-TECH-SNAPSHOT-..."}
}
```

An empty loaded-symbol count returns `PARTIAL`; it cannot claim valid
`SUCCESS`.

## Market Outlook and Final Watchlist examples

Market Outlook status includes global market snapshot ID, IHSG/regime context,
coverage, provider mode/status, source-health telemetry, warnings and output
artifact paths. Final Watchlist includes current dependency statuses and
technical/broker/multi-day snapshot IDs; a stale or missing predecessor causes
`SKIPPED`/dependency failure.

## Final Decision contract

`CanonicalCandidate` contains only the requested score, risk, blocker, warning,
provenance and snapshot fields. `final_action` is restricted to `BUY`, `WATCH`,
`WAIT`, `AVOID`, or `NO_DATA`. Source provenance and snapshot IDs are mandatory;
Stage 2 thresholds and protected decision columns remain owned by the existing
Decision Engine.

## Telegram routing

`SIGNAL`: final watchlist, final decision, signal detail, entry/stop/targets.  
`REPORT`: market outlook, post market, broker summary, broker multi-day.  
`SYSTEM`: startup, warning, health, dependency, and configuration errors.

Thread IDs come from `TELEGRAM_THREAD_SIGNAL_ID`,
`TELEGRAM_THREAD_REPORT_ID`, and `TELEGRAM_THREAD_SYSTEM_ID`; an empty ID uses
the main chat. Delivery audit records include report type, target thread,
message thread ID, Telegram message ID, part count, idempotency key and status.

## Cleanup and security

`.env`, runtime state, caches, locks, previews, snapshots, job status, output,
logs, temporary CSV/JSON, and coverage caches are ignored. The local Telegram
configuration was sanitized to empty values; credentials are environment-only.
No official test fixture was ignored or removed. No source active file contains
a redaction marker or hardcoded credential.

## Validation and compatibility

The required runtime/config/contract checks, compilation, full pytest suite,
`git diff --check`, and Git status are run before delivery. Legacy Stage 1/2
imports and tests remain supported; `master_pipeline.py` is explicitly marked
deprecated rather than removed. Live ZAPI payload mapping, authentication,
rate limits, real corporate actions, live market freshness, and provider
conflict behaviour still require ZAPI documentation and live market data.

