# Telegram Delivery Audit Prevention Runbook

Status: normative operational documentation  
Scope: every SDE Swing report, notification, preview, resend, recovery, and
Telegram acknowledgement path.  
Audit baseline: branch `testing`, commit
`6da1791b514de24ed5ad49edff0b66c6477e1c15`.

This document is a release and incident-control contract. It records the rules
that must hold before a Telegram flow is called safe. It does not make an
unsafe implementation safe by itself; open findings in the register below must
be closed in code and covered by tests before the verdict can become `SAFE`.

## 1. Non-negotiable outcomes

Every operational Telegram flow must satisfy all of these invariants:

1. One canonical engine run produces immutable report artifacts. Preview,
   resend, and recovery consume those artifacts; they never recalculate engine
   decisions or mutate `LATEST` state.
2. The active path is
   `ReportPayload -> TelegramRouter -> modules/job_runner/delivery.py`.
   A new launcher must not call Telegram HTTP directly.
3. `SENT` means that Telegram returned `ok=true` **and** a positive integer
   `result.message_id` for every part. A missing, empty, non-numeric, or zero
   message ID is not success.
4. A timeout or connection error after a request may have reached Telegram is
   `DELIVERY_STATE_UNCERTAIN`, never an automatic retry.
5. A multipart report is one logical delivery. A partial acknowledgement is
   fail-closed and requires manual investigation; replaying the whole bundle
   can duplicate already accepted parts.
6. Lifecycle events are acknowledged only after the common delivery layer
   returns visible success. A local return code alone is not an acknowledgement.
7. Concrete per-report routes have precedence over generic environment routes.
   News, IDX, and Watchlist AI may not fall back to the main chat.
8. Idempotency is atomic and includes the source run, trade date, report type,
   route, payload signature, part sequence, and attachment content hash.
9. Preview and resend must show/send the same canonical bytes selected by the
   same immutable receipt. A mutable `LATEST` file is never an exact-replay
   source.
10. Compatibility-only senders remain quarantined. They may not be reused by a
    new scheduler, menu, maintenance command, or report family.

## 2. Canonical architecture

```text
engine-owned decision state
        |
        v
immutable run artifacts + manifest + hashes
        |
        v
canonical formatter -> ReportPayload
        |
        v
TelegramRouter (validated numeric route)
        |
        v
common delivery layer (idempotency, send, IDs, state, logs)
        |
        v
Telegram topic

delivered archive / zero-ACK recovery archive
        |
        +--> Preview Existing (read-only)
        +--> Resend (copyMessage first, exact fallback)
        +--> Recovery (direct archive only after all safety checks)
```

The canonical active components are documented in
[`TELEGRAM_ROUTING.md`](TELEGRAM_ROUTING.md). The following are mandatory
boundaries:

- engine ownership: `run_sde_job.py`, `modules/job_runner/core.py`;
- report construction: `modules/job_runner/enhanced_daily_reports.py` and the
  report-specific builders;
- routing: `modules/telegram/router.py`;
- common transport/state: `modules/job_runner/delivery.py`;
- exact delivered replay: `modules/job_runner/existing_delivery.py` and
  `tools/resend_final_watchlist.py`;
- zero-ACK Final Watchlist recovery:
  `tools/resend_final_watchlist_recovery.py` plus its module helper.

## 3. Delivery inventory and ownership

| Family | Canonical producer | Delivery/replay owner | Required isolation |
|---|---|---|---|
| Market Outlook | `enhanced_daily_reports.py` | common delivery; `resend_daily_report.py` | REPORT topic |
| Post Market text | `enhanced_daily_reports.py` | common delivery; `resend_daily_report.py` | REPORT topic |
| Post Market heatmap | report attachment builder | common delivery + guard | same Post Market topic |
| Broker Summary / CSV | broker report builders | common delivery | REPORT topic |
| Final Watchlist summary/detail/CSV | Final Watchlist entrypoint | common delivery; exact resend/recovery tools | one SIGNAL family/topic |
| Signal Detail | signal report builder | common delivery | configured Signal topic |
| System, warning, dependency | runtime/system reporters | common delivery | SYSTEM topic; no report fallback |
| Active Recommendations | lifecycle/presentation builder | common delivery; failure must be visible | REPORT topic |
| Performance / Lifecycle Digest | lifecycle presentation | common delivery; ack after visible send | REPORT topic |
| Watchlist AI interpretation/status | `tools/run_watchlist_ai.py` | common delivery | dedicated numeric AI topic only |
| Morning/Post Market News | `modules/news/*` | isolated news sender or migrated common delivery | NEWS topic; no main-chat fallback |
| IDX Disclosure | `modules/idx_disclosure/*` | IDX adapter with positive-ID/edit contract | IDX topic; no main-chat fallback |
| Position Management | position management runtime | common delivery | explicit Position route, not raw scheduler text |
| Legacy Swing bundle | `master_pipeline.py` compatibility path | compatibility only | no new callers |

When a new report is added, it must be inserted in this inventory before code
review is complete. "It uses the same bot" is not sufficient evidence that it
uses the same safety contract.

## 4. Mode contract: build, preview, resend, recovery

| Mode | May run engine? | May mutate engine state? | Source | Telegram call? | Required result |
|---|---:|---:|---|---:|---|
| Normal build/send | Yes, once | Yes, through normal run context | current run artifacts | Yes | all parts positive IDs or explicit uncertainty |
| Preview current artifact | No | No | current immutable artifact | No | rendered bytes and hashes only |
| Preview Existing | No | No | selected archived receipt | No | exact source/run/date/route receipt |
| Resend delivered source | No | No | fully delivered archive | Yes (`copyMessage` first) | copied positive IDs for every part |
| Zero-ACK recovery | No | No | same-date `FAILED` source with zero IDs | Yes, direct archive | hash-locked direct replay |
| Partial/uncertain recovery | No | No | any source with an ACK or uncertainty | **No automatic send** | manual investigation record |

Before release, prove Preview Existing is engine-free with a test that fails if
any decision, broker, exit, discovery, formatter, database archive, or `LATEST`
write is reached. The integrated `--no-telegram` flag alone is not proof of an
artifact-only preview.

## 5. Route contract

The repository scheduler fallback is the following logical map:

| Family | Expected numeric topic |
|---|---:|
| Market Outlook, Post Market, Broker Summary, Final Watchlist family | 9 |
| Signal Detail | 6 |
| Evaluation / generic report | 701 |
| System | 5 |
| Morning/Post Market News | 1451 |
| Watchlist AI | `TELEGRAM_THREAD_AI_ID` (required; no repository fallback) |

Route resolution must follow this order:

1. explicit numeric per-report route;
2. explicit numeric scheduler fallback for the report type;
3. category-specific validation;
4. otherwise fail closed.

The following are release blockers:

- a symbolic value such as `"SIGNAL"` is accepted as a thread ID;
- a generic `TELEGRAM_THREAD_SIGNAL_ID` overrides an explicit Final Watchlist
  route;
- `NEWS`, IDX, or AI silently falls back to the main chat;
- a scheduler fallback is copied as an unvalidated string (for example
  `"NEWS"`) into `message_thread_id`;
- the same logical Final Watchlist family is split across topics.

Test route resolution with at least these configurations: concrete numeric
route present; concrete route absent with numeric scheduler fallback; symbolic
config value; generic SIGNAL environment set; AI topic absent; NEWS/IDX topic
absent. The expected result for every invalid case is a non-send outcome.

## 6. Delivery state machine

```text
PREPARED
   |
   v
SENDING -- all parts: ok=true + positive message_id --> SENT
   |
   +-- no remote acknowledgement ---------------------> FAILED (recoverable
   |                                                      only if all rows FAILED,
   |                                                      zero IDs, same date,
   |                                                      complete hash manifest)
   |
   +-- timeout/connection after possible acceptance ---> DELIVERY_STATE_UNCERTAIN
   |
   +-- some parts acknowledged -----------------------> PARTIAL / manual only
```

Required persistence rules:

- write a delivery event for each part, including report type, run ID, route,
  sequence, signature, status, and Telegram ID;
- reserve the idempotency key atomically before sending;
- mark the logical report delivered only when every expected part is `SENT`;
- never convert a missing Telegram ID into an empty-string success;
- never retry an uncertain or partially acknowledged logical bundle automatically;
- acknowledge lifecycle events only after this state transition is durable.

Photo-to-text fallback is allowed only when the photo request is known not to
have reached Telegram. A timeout or transport exception is ambiguous and must
not be followed by an automatic second send.

## 7. Idempotency and exact replay rules

The idempotency/replay key must be derived from:

```text
(source_run_id, trade_date, report_type, route, delivery_signature,
 part_sequence, attachment_sha256)
```

The following are not safe keys on their own:

- report type plus date;
- attachment filename only;
- delivery sequence without signature/source run;
- a mutable `LATEST` path;
- a preview path derived from raw source text while resend uses canonical text.

Every exact source must have:

- a run-scoped preview for each selected payload;
- the canonical text/HTML and every attachment archived;
- SHA-256 for each payload and attachment in the manifest;
- route, report type, part order, and source Telegram IDs in the receipt;
- an immutable selection receipt used by both Preview Existing and Resend.

`copyMessage` is the preferred resend operation for a fully delivered source.
If it fails, the hash-locked archived fallback must be preflighted for the
whole bundle before part 1 is sent. A source with any Telegram acknowledgement
must not enter zero-ACK recovery.

## 8. Direct-sender quarantine

The following patterns require an explicit exception review and a test proving
equivalent safety before they can be used by an active flow:

- `requests.post`/`urllib` directly to Telegram outside the common delivery
  adapter;
- marking lifecycle events notified after a process return code only;
- swallowing a report delivery exception and returning an overall success;
- a legacy `telegram_bot.py` sender called from a new launcher;
- a force flag that bypasses route, idempotency, or hash checks.

Compatibility code may remain for historical regression contracts, but its
callers must be listed in `LEGACY_FILE_MANIFEST.md` and no maintenance menu or
scheduler may introduce a new dependency on it.

## 9. Release gate and test protocol

### 9.1 Repository identity

Run these commands before reviewing behavior:

```powershell
git status --short --branch
git branch --show-current
git rev-parse HEAD
```

Record the exact output in the release evidence. A branch or SHA mismatch
invalidates the audit result.

### 9.2 Mandatory regression suite

Run the delivery/replay contract tests without Telegram credentials or live
network access:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_delivery_idempotency_phase2.py `
  tests/test_exact_delivery_replay.py `
  tests/test_resend_daily_reports.py `
  tests/test_resend_final_watchlist.py `
  tests/test_final_watchlist_failed_delivery_recovery.py `
  tests/test_final_watchlist_preview_snapshot_contract.py `
  tests/test_final_watchlist_snapshot_replay.py `
  tests/test_lifecycle_preview_resend.py `
  tests/test_lifecycle_live_digest_ack_boundary.py `
  tests/test_post_market_delivery_guard.py `
  tests/test_post_market_current_contract.py `
  tests/test_idx_integrated_telegram.py `
  tests/test_idx_telegram_edit.py `
  tests/test_telegram_router.py `
  tests/test_telegram_router_specific_report_precedence.py `
  tests/test_v170_telegram_contract.py
```

Release criterion: zero failures, zero unexpected skips. Any test that asserts
the old launcher/menu behavior must be updated together with the implementation;
do not weaken the assertion merely to restore green CI.

### 9.3 Baseline evidence (2026-08-27)

The audit baseline was not green. The mandatory delivery/replay command
reported `75 passed, 3 failed`; the supplemental launcher/news/scheduler/AI
command reported `142 passed, 3 failed`; collection reported `842 tests
collected`. The incomplete full-suite attempt has no release result and must be
rerun to completion. The three mandatory failures cover the Final Watchlist
preview receipt/menu contract and lifecycle preview menu wording; the
supplemental failures also cover legacy Final Watchlist entrypoint/menu and a
control-center branding assertion. These failures are release blockers, not
documentation-only exceptions.

### 9.4 Required failure-injection cases

Use mocked Telegram responses; never use a real bot token in CI:

| Case | Expected state | Automatic resend? |
|---|---|---:|
| `{ok:true,result:{message_id:123}}` | `SENT` | no |
| `{ok:true,result:{}}` | `FAILED`/invalid response | no |
| HTTP error before request leaves process | `FAILED`, zero IDs | only through guarded recovery |
| timeout after request may be accepted | `DELIVERY_STATE_UNCERTAIN` | no |
| photo timeout | uncertain, no text fallback | no |
| multipart part 1 ID, part 2 timeout | partial/uncertain | no |
| two concurrent identical sends | one reservation, one logical delivery | no duplicate |
| symbolic route or missing AI topic | skipped/blocked | no |
| lifecycle send failure | event remains unacknowledged | no silent success |
| preview-existing invocation | engine call count remains zero | N/A |

### 9.5 Static review

For every PR touching reports, menus, scheduler, or Telegram, review at least:

```powershell
rg -n "requests\.(post|get)|urllib|sendMessage|sendPhoto|copyMessage" modules tools *.py
rg -n "message_id|DELIVERY_STATE_UNCERTAIN|mark_lifecycle_events_notified" modules tools
rg -n "preview-existing|LATEST|TELEGRAM_THREAD_SIGNAL_ID" .
```

Each match must be classified as canonical, isolated adapter, or
compatibility-only. An unclassified direct sender blocks merge.

## 10. Operator incident runbook

1. Capture branch, SHA, command, trade date, report type, route, and local run
   ID. Do not rerun the engine just to obtain a preview.
2. Inspect delivery rows and the manifest. Distinguish `SENT`, `FAILED`,
   `DELIVERY_STATE_UNCERTAIN`, and partial acknowledgement by actual positive
   Telegram IDs.
3. If any part has an ID or is uncertain, stop automatic resend. Record the
   possible Telegram message IDs and investigate the target topic manually.
4. If every selected row is `FAILED`, has zero IDs, is for the requested date,
   and the complete archive/hash manifest is valid, use the dedicated Final
   Watchlist recovery path. Recovery must not run engine, formatter, or mutable
   artifact generation.
5. If the source bundle is incomplete, hash-mismatched, mixed-signature, or
   from another date, stop and create an incident; do not force-resend.
6. After any manual action, record the outcome and source IDs in the incident
   evidence. Add a regression test before closing the incident.

The current `maintenance/SYSTEM_MENU.bat` preview-existing path is not an
acceptable safety proof while it still invokes the integrated engine path. Use
the artifact-only/exact replay tools only after their source and receipt checks
are visible in the command output.

## 11. Open findings carried by the audit baseline

These findings explain why the baseline cannot be declared safe. Each item
must be tracked to a code change and a regression test.

### F1 — CRITICAL: Preview Existing can run the engine

- **Location:** `maintenance/SYSTEM_MENU.bat:66-72`,
  `run_sde_job_integrated.py:80-111,169-214`, `run_sde_job.py:565-566`,
  `modules/job_runner/core.py:1252-1454`.
- **Current:** the menu passes `--preview-existing`, but the integrated child
  still reaches `run_final_from_snapshot` and engine-owned work; `--no-telegram`
  suppresses sending only.
- **Expected:** Preview Existing reads a complete archived receipt and never
  starts broker, decision, exit, discovery, formatter, or database mutation.
- **Reproduction:** run the menu preview command with engine-call/write spies;
  the child process is still created.
- **Impact:** a supposedly read-only preview can recalculate or mutate state.
- **Root cause:** preview intent is not a first-class artifact-only execution
  mode in the integrated entrypoint.
- **Required fix:** route the menu to an artifact-only selector and add a
  zero-engine-call regression test.

### F2 — CRITICAL: Common delivery accepts a missing Telegram ID

- **Location:** `modules/job_runner/delivery.py:320-327,560-621`.
- **Current:** HTTP/body `ok` is enough; an empty `result.message_id` can be
  persisted while the logical event becomes `SENT`.
- **Expected:** positive integer ID is mandatory for every part.
- **Reproduction:** mock `{ok:true,result:{}}`; observe `SENT` with `""` ID.
- **Impact:** false success, lost recovery eligibility, and duplicate risk.
- **Root cause:** ID validation exists in exact replay but not normal delivery.
- **Required fix:** centralize strict response validation and test all malformed
  success bodies.

### F3 — HIGH: Uncertainty and multipart retry can duplicate

- **Location:** `delivery.py:501,605-660`, `run_sde_job.py:113-145`.
- **Current:** generic failures may be marked `FAILED`, and a scheduler retry
  can resend the whole payload; `DELIVERY_STATE_UNCERTAIN` is not consistently
  terminal to the caller.
- **Expected:** timeout/ambiguous transport is terminal for automatic retry;
  partial bundles are manual-only.
- **Reproduction:** accept part 1, timeout part 2, then run scheduler retry.
- **Impact:** duplicate Telegram messages and misleading job status.
- **Root cause:** crash ambiguity is documented but not enforced at every
  caller boundary.
- **Required fix:** propagate uncertainty as a non-retry terminal state and
  reserve explicit operator recovery.

### F4 — HIGH: Photo fallback and normal FAILED recovery are unsafe

- **Location:** `delivery.py:549-581`,
  `modules/job_runner/final_watchlist_recovery.py:109-206`.
- **Current:** any photo exception may trigger text fallback; a normal FAILED
  timeout with no local ID can be treated as zero-ACK recovery.
- **Expected:** fallback only after confirmed non-acceptance; normal ambiguous
  failures must not enter automatic recovery.
- **Reproduction:** make `sendPhoto` time out after remote acceptance, or make a
  normal send fail before writing an ID, then invoke recovery.
- **Impact:** photo plus text duplicates or whole-bundle duplicates.
- **Root cause:** transport exception is conflated with proof of non-delivery.
- **Required fix:** distinguish pre-send, post-send-unknown, and confirmed
  rejected states in the transport contract.

### F5 — HIGH: Final Watchlist menu is not one exact source of truth

- **Location:** `RUN_FINAL_WATCHLIST.bat:32-74`,
  `maintenance/SYSTEM_MENU.bat:66-72`,
  `tools/resend_final_watchlist.py:330-334`.
- **Current:** menu options can select current/snapshot artifacts or an
  integrated preview rather than the last canonical delivered/recoverable
  receipt; raw source selection can differ from canonical resend selection.
- **Expected:** Preview Existing, Resend, and Recovery share one date-locked,
  hash-locked selection receipt.
- **Reproduction:** compare the menu preview text/path with the source selected
  by exact resend for the same date.
- **Impact:** operator previews one report and resends another.
- **Root cause:** multiple legacy entrypoints and mutable/current artifact paths.
- **Required fix:** retire ambiguous menu branches and make the receipt the only
  source for exact operations.

### F6 — HIGH: Lifecycle, Active Recommendations, and Performance have direct senders

- **Location:** `tools/send_lifecycle_digest.py:40-66`,
  `modules/outcome_tracker_baseline.py:2711-2767`,
  `tools/send_active_recommendations.py:47-63`.
- **Current:** direct HTTP sends bypass common routing, idempotency, strict ID
  checks, and durable delivery logs; some failures are swallowed while the
  parent job remains successful.
- **Expected:** all active report sends use `ReportPayload`, router, common
  delivery, and acknowledge lifecycle only after visible success.
- **Reproduction:** mock a successful HTTP body with no ID or a send exception;
  observe local success/notified state.
- **Impact:** duplicates, wrong topic, and lost lifecycle events.
- **Root cause:** legacy sender retained as an active operational path.
- **Required fix:** migrate or explicitly isolate these callers and add ack/
  failure propagation tests.

### F7 — HIGH: News send is non-atomic and bypasses the common contract

- **Location:** `modules/news/news_monitor.py:681-738`,
  `modules/news/news_monitor_market_impact.py:903-974`.
- **Current:** signature check-then-send is raceable; IDs may be `None`; a
  middle failure can leave duplicates on retry.
- **Expected:** atomic reservation, positive IDs, bounded splitting, and an
  explicit NEWS route with no main-chat fallback.
- **Reproduction:** run two concurrent identical news sends or fail part 2.
- **Impact:** duplicate news and unreconciled delivery state.
- **Root cause:** isolated legacy sender does not share common delivery state.
- **Required fix:** migrate to the common contract or implement equivalent
  atomic/id/uncertainty semantics with dedicated tests.

### F8 — HIGH: Recovery does not prove a complete, unique bundle

- **Location:** `final_watchlist_recovery.py:113-177,186-203`,
  `existing_delivery.py:392-460`.
- **Current:** selection can accept incomplete payload evidence, and sequence
  identity can be reused with a different signature/source.
- **Expected:** every expected part, canonical payload, attachment, route, and
  signature is present exactly once in the receipt.
- **Reproduction:** remove one archive part or create two rows with the same
  sequence and different signatures, then run selection.
- **Impact:** incomplete or duplicate replay.
- **Root cause:** validation is row-oriented rather than complete-bundle and
  uniqueness-oriented.
- **Required fix:** enforce manifest cardinality, signature uniqueness, and
  source-run identity before any send.

### F9 — MEDIUM: Archive/canonical/hash and attachment keys are incomplete

- **Location:** `existing_delivery.py:335-353,392-460`,
  `tools/resend_final_watchlist.py:330-334`, `delivery.py:59-60`.
- **Current:** some legacy/partial archives lack manifest hashes; preview can
  retain raw source while resend canonicalizes; attachment idempotency uses a
  filename-based key.
- **Expected:** all bytes are canonicalized before selection and every payload/
  attachment content hash participates in identity.
- **Reproduction:** change bytes under the same filename or compare raw/canonical
  preview and resend receipts.
- **Impact:** stale or wrong attachment/report replay and suppressed new content.
- **Root cause:** compatibility archive formats predate the full manifest
  contract.
- **Required fix:** require complete hashes for new sources and reject legacy
  sources without an explicit migration receipt.

### F10 — HIGH: Generic SIGNAL environment can hijack a report route

- **Location:** `modules/telegram/router.py:73-126`,
  `config/telegram.json:33-63`, `config/scheduler.json:210-227`.
- **Current:** when a concrete route is absent, a generic
  `TELEGRAM_THREAD_SIGNAL_ID` can be accepted; the checked-in config contains
  symbolic `"SIGNAL"`, while scheduler expects numeric topic `9` for Final
  Watchlist.
- **Expected:** concrete report route or numeric scheduler fallback wins;
  symbolic/generic values cannot hijack Final Watchlist.
- **Reproduction:** set `TELEGRAM_THREAD_SIGNAL_ID=999` and resolve
  `final_watchlist_summary` without a numeric per-report route.
- **Impact:** report appears in an unintended topic.
- **Root cause:** compatibility fallback remains reachable in active resolution.
- **Required fix:** remove generic fallback from active categories or require an
  explicit compatibility mode, plus config-matrix tests.

### F11 — MEDIUM: Scheduler fallback and Position Management routes are weakly typed

- **Location:** `modules/job_runner/delivery.py:226-235,350-354`,
  `position_management_engine.py:884-895`.
- **Current:** raw scheduler topic strings can reach Telegram; Position
  Management has no equivalent explicit delivery route in the legacy path.
- **Expected:** all topics are validated positive integers and every report has
  one explicit route or a documented safe fallback.
- **Reproduction:** configure fallback `"NEWS"` or emit `POSITION_MANAGEMENT`
  through the legacy builder.
- **Impact:** wrong topic or accidental main-chat delivery.
- **Root cause:** route configuration and payload labels use different types.
- **Required fix:** validate at router boundary and add a Position route mapping.

### F12 — MEDIUM: Post Market guard does not cover heatmap attachment failure

- **Location:** `post_market_delivery_guard.py:27,53-61`,
  `delivery.py:82-138`.
- **Current:** the guard hardens text `POST_MARKET`, while heatmap/finalization
  failures can leave a retryable job that sends the text again.
- **Expected:** text and heatmap form one guarded logical bundle.
- **Reproduction:** fail heatmap generation/finalization after text is accepted,
  then retry the scheduler job.
- **Impact:** duplicate Post Market text or mismatched attachment.
- **Root cause:** attachment is not included in the guard's terminal state.
- **Required fix:** guard the complete report bundle and persist part-level state.

### F13 — MEDIUM: IDX watcher lacks the full common length/ID contract

- **Location:** `modules/idx_disclosure/telegram_delivery.py:136-190`,
  `modules/idx_disclosure/formatter.py:41-64`.
- **Current:** the primary adapter validates IDs, but watcher/custom paths and
  long official messages do not uniformly enforce the common bounds.
- **Expected:** every IDX path has positive IDs, safe splitting, edit semantics,
  and no main-chat fallback while preserving official text unchanged.
- **Reproduction:** send a long document or use the watcher custom delivery
  callback with a malformed response.
- **Impact:** rejected/partial disclosure delivery or untracked state.
- **Root cause:** IDX has parallel adapters with different contracts.
- **Required fix:** centralize transport validation and add long-message and
  malformed-response tests without changing official wording.

### F14 — MEDIUM: Compatibility `telegram_bot.py` remains a bypass risk

- **Location:** `modules/telegram/telegram_bot.py:88-136,437-610`,
  `master_pipeline.py:574-600`.
- **Current:** the legacy sender has separate logging/routing/idempotency and
  remains reachable through the compatibility pipeline.
- **Expected:** no new operational caller; remaining caller is clearly marked,
  monitored, and covered by an explicit migration test.
- **Reproduction:** invoke the compatibility Full Manual path and compare its
  route/state behavior with common delivery.
- **Impact:** behavior differs by launcher and can regress silently.
- **Root cause:** historical entrypoint has not been fully retired.
- **Required fix:** quarantine, migrate, then remove only after compatibility
  evidence proves parity.

## 12. Definition of done

A Telegram delivery change may be released as `SAFE` only when:

- no CRITICAL or HIGH finding remains open;
- the targeted and supplemental suites pass with zero failures;
- the full suite completes and its result is recorded;
- failure-injection tests cover missing IDs, timeout ambiguity, multipart
  partials, duplicate concurrency, route hijack, and preview engine calls;
- every active sender is classified and uses the canonical contract;
- Preview Existing is demonstrably engine-free and receipt-driven;
- route configuration is numeric, explicit, and isolated by category;
- incident/recovery behavior is fail-closed and operator-visible;
- the exact branch and SHA are recorded in the release evidence.

At the audit baseline named at the top of this document, the correct verdict is
`NOT SAFE`; this runbook is the prevention and acceptance contract for the fixes,
not a claim that those findings have already been remediated.
