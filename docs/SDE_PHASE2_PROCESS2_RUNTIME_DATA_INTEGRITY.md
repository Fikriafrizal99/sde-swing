# SDE Phase 2 - Process 2 Runtime and Data Integrity Hardening

## Baseline and Scope

- Branch: `audit/sde-stabilization`
- Direct parent / Phase 2 Commit 1 HEAD:
  `de8bafe7485c4926bbc85b8575e8ac245934bf89`
- Mode: implementation
- Commit ownership: runtime and data integrity only
- Closure state: implementation evidence pending Phase 2 re-audit

This process owns exactly these audit findings:

- `AF-P2-001` - Telegram idempotency race;
- `AF-P2-003` - historical database revision immutability;
- `NF-C2-001` - generic atomic CSV temporary-file collision;
- `NF-C2-002` - generic JSON atomicity;
- `NF-C4-006` - traceback path normalization;
- `NF-C5-004` - BEI calendar injection.

It does not own Phase 2 Process 3 findings, including `PA2-NF-001` and
`PA2-NF-002`.

## Frozen Contracts

No scoring, weight, threshold, blocker, candidate-selection, entry, SL, TP,
RR, or lifecycle semantic is changed. The protected quant-source set is not
modified. `MODERATE_BASELINE` and `SHADOW_ONLY` remain active, and
`auto_entry_enabled` remains `false`.

## Runtime/Data Architecture After Process 2

```text
ReportPayload
  -> delivery preconditions
  -> SQLite atomic reservation
  -> Telegram send attempt
  -> SENT or FAILED transaction
  -> compatibility JSON projection + delivery JSONL

Historical CSV revision
  -> row normalization and hashes
  -> append-only revision table
  -> backward-compatible current projection

Runtime artifact producer
  -> unique same-directory temporary file
  -> write + flush + fsync
  -> atomic os.replace
  -> best-effort temporary cleanup

Runner exception
  -> write_traceback(ctx, suffix)
  -> ctx.status_root/tracebacks
  -> status traceback_path

Canonical record
  -> DataSourceManager calendar injection
  -> record-type-aware quality validation
  -> conflict resolution / canonical consumer
```

## AF-P2-001 - Telegram Idempotency

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

### Executable Path

`run_sde_job.py` / `run_sde_job_integrated.py` -> report builder ->
`modules.job_runner.delivery.deliver()` -> `DeliveryIdempotencyStore` ->
Telegram sender -> state transaction -> lifecycle acknowledgement.

The former JSON check -> remote send -> JSON write sequence is no longer the
authoritative state transition. The authoritative sidecar is SQLite; the
existing JSON index remains a compatibility projection of `SENT` rows.

Default paths:

- compatibility projection:
  `data/state/scheduler/telegram_idempotency.json`;
- authoritative database:
  `data/state/scheduler/telegram_idempotency.sqlite3`;
- append delivery evidence:
  `data/state/scheduler/delivery_log.jsonl`.

Both database path and reservation TTL can be configured under scheduler
`delivery` settings.

### Reservation State Model

| Existing state | Normal caller | Force caller | Result |
|---|---|---|---|
| no row | reserve | reserve | one owner obtains `RESERVED` |
| live `RESERVED` | suppress | suppress | `DELIVERY_IN_PROGRESS`; force cannot steal a live lease |
| stale `RESERVED` | reclaim | reclaim | prior attempt becomes `STALE_RECLAIMED`; new owner reserves |
| `FAILED` | retry | retry | new attempt reserves |
| `SENT` | suppress | reserve | normal duplicate is suppressed; force creates an explicit resend attempt |

`BEGIN IMMEDIATE` serializes reservation decisions. Ownership is bound to both
an attempt ID and owner token. Lease renewals occur before each remote part.
Only the current owner can commit `SENT` or `FAILED`; a late stale owner cannot
overwrite a reclaimed attempt.

Successful remote delivery is committed to SQLite before lifecycle-event ACK.
Therefore a lifecycle ACK failure does not trigger a Telegram resend; a later
duplicate path can retry the ACK without sending the report again. Failed sends
are retryable and every attempt remains queryable in
`telegram_delivery_attempts`.

### Delivery Guarantee

The implementation provides atomic local reservation and an
`AT_LEAST_ONCE_WITH_CRASH_AMBIGUITY` delivery contract. It does **not** claim
exactly-once Telegram delivery. If a process dies after Telegram accepts a
message but before local `SENT` commit, the lease eventually becomes stale and
a retry can duplicate the remote message. Telegram does not provide a matching
idempotency-key transaction that could close this window.

## AF-P2-003 - Historical Price Revisions

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

`market_prices_daily` remains the backward-compatible current projection with
its existing `(symbol, price_date, source)` identity. It now points to the
selected revision through `revision_id` and `revision_sequence`.

`market_prices_daily_revisions` is the append-only history. It stores:

- monotonic `revision_sequence`;
- deterministic `revision_id`;
- symbol/date/source identity and OHLCV values;
- provider/source revision;
- source path, row hash, archive timestamp, and creation timestamp.

Every new file revision is inserted into history first. Only a newly inserted
revision updates the current projection. `market_prices_daily_latest` provides
an explicit latest/current view while old readers may continue querying
`market_prices_daily` unchanged.

Existing databases are migrated in place: their current rows are copied into
history with `LEGACY_MIGRATION` provenance and linked back to their generated
revision IDs. Existing history cannot be reconstructed beyond the row that
survived before this migration.

Database triggers reject `UPDATE` and `DELETE` on the revision table with
`MARKET_PRICE_REVISION_APPEND_ONLY`. Regression tests prove revision A and B
remain available, the current reader selects B, duplicate ingestion does not
create another revision, migration succeeds, and `PRAGMA integrity_check`
returns `ok`.

## NF-C2-001 and NF-C2-002 - Generic Artifact Durability

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

The shared text, CSV, dictionary-CSV, and JSON writers now use this protocol:

1. create a unique `.<name>.<pid>.<uuid>.tmp` in the destination directory;
2. write the complete payload;
3. flush the language/runtime buffer;
4. `fsync` the temporary file;
5. publish with `os.replace`;
6. clean any remaining temporary file on success or failure.

Same-directory publication keeps temporary and destination files on the same
filesystem. Concurrent publishers cannot collide on a deterministic temp name.
On Windows, bounded retry handles transient `WinError 5/32` sharing conflicts
during simultaneous atomic replacement.

Active runtime callers that previously wrote JSON/CSV state, cache, manifests,
snapshots, analytics summaries, broker-period context, market metadata, news,
position-management artifacts, and validated-source outputs directly now use
the durable shared writers. Append-only JSONL logs remain append streams and
are not misrepresented as replace-style JSON documents.

The stronger stabilization protections remain intact:

- `FINAL_DECISION_V2.manifest.json` still performs its stale canonical-hash
  rejection before atomic publication;
- runtime engine/delivery status retains its dedicated unique-temp status
  publisher and channel separation.

Concurrency tests prove that the final CSV or JSON is one complete publisher's
payload. Interruption tests prove an old destination survives a failed replace
and temporary files are cleaned. `fsync` is asserted for JSON, DataFrame CSV,
and dictionary CSV.

## NF-C4-006 - Traceback Paths

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

`modules.job_runner.runtime.write_traceback()` is now the common path/publish
helper. Every active exception path with a runtime context writes below:

`ctx.status_root / "tracebacks"`

This includes post-market stage exceptions, ordinary unhandled job
exceptions, lock-boundary failures, integrated-runner failures, and lock-exit
interrupt/unhandled terminalization. Each resulting `traceback_path` is the
path actually written.

The default scheduler path remains `data/output/job_status`, so default
behavior is backward-compatible. Custom scheduler status roots are now honored
uniformly.

## NF-C5-004 - BEI Calendar Injection

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

`DataSourceManager` loads `config/trading_calendar.json` by default and injects
its `holidays` and `special_trading_days` into the canonical
`DataQualityEngine`. `RunnerContext` passes its already-loaded scheduler
calendar; `RuntimeContext.create()` resolves the scheduler-selected calendar,
so custom runtime calendar configuration is preserved.

`NON_TRADING_DAY` is now applied only to exchange-session-bound records:

- `DailyBar`;
- `IntradayQuote`;
- `OrderBookSnapshot`;
- `BrokerFlow`;
- `ForeignFlow`;
- `TradingStatus`;
- `MarketIndex`.

It is not blindly applied to `CorporateAction`, `SymbolMetadata`, or a generic
non-session envelope. Configured special sessions override weekend/holiday
closure through the existing IDX calendar function. Tests cover an ordinary
weekday, configured holiday, ordinary weekend, special Saturday session,
non-market metadata on closed dates, and a DailyBar whose expected date is a
closed session.

## New Finding During Implementation

### P2P2-NF-001 - Windows concurrent atomic replace sharing conflict

- Severity: P2
- Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**
- Evidence: initial concurrency tests produced transient `WinError 5` when
  multiple publishers replaced the same destination simultaneously.
- Disposition: blocking and directly within generic artifact durability, so a
  bounded Windows-only replace retry was added. A reader may still receive a
  transient Windows sharing denial; no partial document is published.

No additional finding is deferred by Process 2.

## Targeted Verification

| Section | Command scope | Result |
|---|---|---|
| A - Telegram | idempotency + runner/scheduler delivery regressions | **32 passed** |
| B - DB | revision migration/history + archive/portfolio regressions | **21 passed** |
| C - artifacts | durability + V2/status + active caller regressions | **40 passed** |
| D - traceback | custom-root boundaries + lifecycle/status regressions | **18 passed** |
| E - calendar | BEI calendar + quality/router/runtime/post-market regressions | **43 passed** |

## Final Validation

| Validation | Result |
|---|---|
| `python -m compileall -q .` | PASS |
| Process 2 targeted aggregate | **176 passed** |
| `python tools/ci_validate_quant_freeze.py` | Known `PA2-NF-001` Windows CRLF false failure |
| `python tools/ci_validate_stabilization_release.py` | Stops on the same known `PA2-NF-001` result |
| `python -m pytest -q` | **588 passed, 2 failed, 3 subtests passed** |
| Full-suite failure 1 | Known `PA2-NF-001`: worktree-byte hash differs under Windows CRLF; protected HEAD blobs equal the freeze and baseline |
| Full-suite failure 2 | Known `PA2-NF-002`: Windows path separator in resend assertion |

The protected quant-source diff is empty. No validator, protected quant file,
or known Process 3 test was changed to make a local Windows gate green.

## Future Audit Queries

Telegram state:

```sql
PRAGMA integrity_check;
SELECT idempotency_key, status, run_id, attempt_count, force_count,
       reserved_at, lease_expires_at, updated_at
FROM telegram_delivery_state;
SELECT attempt_id, idempotency_key, run_id, force_resend, status,
       reserved_at, completed_at, error
FROM telegram_delivery_attempts
ORDER BY reserved_at;
```

Historical prices:

```sql
PRAGMA integrity_check;
SELECT symbol, price_date, source, source_revision, revision_sequence,
       row_hash, archived_at
FROM market_prices_daily_revisions
ORDER BY symbol, price_date, source, revision_sequence;
SELECT * FROM market_prices_daily_latest;
```

Runtime evidence checks:

- compare status `traceback_path` parent to the configured `job_status_root`;
- scan destination directories for stale `.<name>.<pid>.<uuid>.tmp` files;
- verify the Telegram JSON projection only as compatibility evidence and use
  SQLite as the authority;
- review calendar source/year/update date before running a new calendar year.

## Residual Risks

- Telegram has an unavoidable remote-accept/local-commit crash window; stale
  recovery can produce a duplicate.
- A multi-part Telegram retry after partial delivery can repeat earlier parts.
- SQLite guarantees assume the sidecar and history database remain on a
  filesystem with correct locking; manual database replacement or privileged
  trigger removal is outside application-level protection.
- Pre-migration historical price revisions that had already been overwritten
  cannot be recovered.
- Windows readers can briefly receive a sharing violation during replacement,
  although they cannot observe a partially written replacement document.
- The BEI calendar is configuration data and must be maintained for future
  years and exchange announcements.

## Quant and Trading Invariance

No trading logic changed.

No quant logic changed.

No autonomous trading capability was enabled.

`auto_entry_enabled` remains `false`.
