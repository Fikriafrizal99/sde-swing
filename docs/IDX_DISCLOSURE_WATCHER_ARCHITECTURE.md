# IDX Disclosure Watcher V1 — Architecture

## Objective
Provide near-real-time notification of new IDX listed-company announcements to Telegram, without scoring, sentiment, trade recommendations, AI interpretation, Brave Search, or writes to SDE decision engines.

## Source Contract
Primary endpoint:

`GET https://www.idx.co.id/primary/ListedCompany/GetAnnouncement`

Expected query parameters:
- `kodeEmiten=` (empty = all issuers)
- `emitenType=s` (`"s"` = Saham hanya; `"*"` = semua jenis)
- `indexFrom=0`
- `pageSize=50`
- `dateFrom=YYYYMMDD`
- `dateTo=YYYYMMDD`
- `lang=id`
- `keyword=`

Observed response contract:
- `ResultCount`
- `SearchParams`
- `Replies[]`
  - `pengumuman.Id2`
  - `pengumuman.NoPengumuman`
  - `pengumuman.TglPengumuman`
  - `pengumuman.JudulPengumuman`
  - `pengumuman.Kode_Emiten`
  - `pengumuman.CreatedDate`
  - `pengumuman.PerihalPengumuman`
  - `attachments[]`
    - `OriginalFilename`
    - `FullSavePath`
    - `IsAttachment`

`Id2` is the primary deduplication key. A deterministic hash of ticker + announcement number + published time is the fallback if IDX changes the identifier contract.

## Architecture

```text
IDX GetAnnouncement
      |
      v
IDXAnnouncementClient
      |
      v
Normalizer
      |
      v
Disclosure model
      |
      v
SQLite repository / dedup
      |
      +-- seen -> skip
      |
      +-- new -> Telegram formatter -> existing Telegram delivery -> mark delivered
```

## Module Boundary

```text
modules/idx_disclosure/
  __init__.py
  client.py        # HTTP source adapter only
  models.py        # normalized immutable contracts
  normalizer.py    # IDX JSON -> internal model
  repository.py    # SQLite state/dedup only
  formatter.py     # Telegram message contract only
  watcher.py       # orchestration; no SDE decision writes

run_idx_disclosure_watcher.py
config/idx_disclosure.json
data/state/idx_disclosure/idx_disclosures.db
logs/idx_disclosure.log
```

The watcher is an information service and must not import or mutate `decision_engine`, `broker_fusion`, technical scoring, entry plans, or AI interpretation.

## Polling Strategy

Default schedule (Asia/Jakarta):
- 08:00–17:00: every 60 seconds
- 17:00–22:00: every 180 seconds
- 22:00–08:00: every 600 seconds

Each poll queries only the current Jakarta calendar date. Normal operation starts with `indexFrom=0&pageSize=50`. Pagination is demand-driven: fetch the next page only when the current page is full and contains unseen records that could extend beyond the page boundary.

Do not loop per ticker.

## First-run Safety

On an empty repository:
1. Fetch the current day's announcements.
2. Store all current records as baseline.
3. Mark them as seen without Telegram delivery.
4. Start notifying only records discovered after baseline creation.

This prevents a notification flood when the watcher is installed mid-day.

## Delivery Semantics

Delivery is at-least-once at source-ingest level and effectively-once at Telegram level through local dedup state plus existing SDE Telegram idempotency.

Record lifecycle:

`DISCOVERED -> STORED -> DELIVERED`

If Telegram delivery fails, the record remains stored with `telegram_sent_at = NULL` and can be retried without re-ingesting the IDX item.

## Failure Isolation

The watcher is non-blocking relative to SDE Swing. IDX timeout, 403, 429, 5xx, schema drift, or Telegram failures must be logged and must never stop the core SDE pipeline.

HTTP defaults:
- persistent session
- timeout: 10s
- conservative retries with exponential backoff
- minimal browser-compatible headers
- no Playwright unless direct HTTP is proven unusable

## Telegram Contract

Example:

```text
📢 IDX KETERBUKAAN INFORMASI

BEEF
20 Agu 2026 • 10:41 WIB

Rencana Penyelenggaraan Public Expose - Insidentil

No. Pengumuman:
B.027-Corpsec-ETT-BEEF-VIII-2026

📄 Dokumen Utama
📎 Lampiran 1

Sumber: Bursa Efek Indonesia
```

Rules:
- no score
- no sentiment
- no BUY/SELL language
- no AI summary
- preserve IDX title
- strip whitespace from ticker
- expose IDX attachment URLs directly; do not download/re-upload PDFs in V1

## Storage Contract

`idx_disclosures`
- `id2 TEXT PRIMARY KEY`
- `ticker TEXT NOT NULL`
- `announcement_no TEXT`
- `published_at TEXT NOT NULL`
- `title TEXT NOT NULL`
- `subject TEXT`
- `idx_created_at TEXT`
- `first_seen_at TEXT NOT NULL`
- `telegram_sent_at TEXT NULL`
- `raw_source TEXT NULL`

`idx_disclosure_attachments`
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `disclosure_id TEXT NOT NULL`
- `filename TEXT NOT NULL`
- `url TEXT NOT NULL`
- `is_attachment INTEGER NOT NULL`
- unique `(disclosure_id, url)`

## V1 Acceptance Criteria
1. Direct HTTP request to IDX returns 200 and parses expected JSON.
2. Empty `kodeEmiten` returns mixed tickers.
3. `Kode_Emiten` is normalized with `.strip()`.
4. `Id2` dedup survives process restart.
5. First run seeds baseline without Telegram flood.
6. New record produces one Telegram notification.
7. Multiple attachments are listed in deterministic order.
8. Failed delivery remains retryable.
9. IDX failure does not affect existing SDE jobs.
10. No Brave/AI call is made by this module.

## Rollout Plan
Phase 1: architecture + contracts + disabled scaffolding.
Phase 2: implement and test direct IDX client against fixtures/live endpoint.
Phase 3: implement repository/normalizer/formatter tests.
Phase 4: dry-run watcher (log only).
Phase 5: Telegram delivery opt-in.
Phase 6: scheduler integration after stability observation.
