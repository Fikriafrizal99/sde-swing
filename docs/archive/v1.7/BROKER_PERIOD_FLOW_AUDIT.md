## Broker Period Flow — Audit & Mapping

> **SUPERSEDED by [docs/BROKER_PERIOD_ARCHITECTURE.md](../../BROKER_PERIOD_ARCHITECTURE.md).**
>
> Archived historical evidence. This file is retained for traceability and is
> not current operational guidance. See `docs/README.md` for active documentation.

Tanggal: 2026-08-11

Tujuan: audit cepat codebase untuk menentukan file / fungsi yang perlu diubah agar Final Watchlist mendukung PRIMARY broker context (1D/3D/5D/CUSTOM), immutable daily archive, internal aggregate building, TODAY PULSE interpretasi, dan provenance.

Ringkasan temuan (tingkat tinggi)
- Pipeline final watchlist di-orchestrate oleh job runner dan modul broker fusion / data_sources.
- Ada engine multi-day (modules/data_sources/broker_multiday_engine.py) dan penyimpanan snapshot (modules/database/swing_history_db.py, data/output/broker_snapshots).
- Ada module util untuk period context: modules/broker_bridge/broker_period_context.py (desain awal untuk period validation/specs).
- Final Watchlist CSV writer: modules/job_runner/enhanced_daily_reports.py (menulis `data/output/final_watchlist/sde-final-watchlist-*.csv`).
- Broker ingestion & normalization: modules/broker_bridge/broker_raw.py, modules/broker_bridge/wait_for_broker_export.py, modules/data_sources/stockbit_adapter.py.
- Broker fusion & scoring: modules/broker_fusion/broker_fusion.py (menggunakan single-session broker summary file), modules/broker_fusion.foreign_flow, modules/broker_fusion.broker_score_frame.
- Multi-day analytics, windows, output: modules/data_sources/broker_windows.py, modules/data_sources/broker_multiday_engine.py, modules/data_sources/broker_multiday_output.py.
- Decision bridge attaches broker output to final decision: modules/data_sources/decision_bridge.py, modules/decision_engine/*, modules/exit_engine/*.
- Telegram/report formatting: modules/telegram/daily_report_ui.py and modules/telegram/telegram_bot.py.

Files of interest (proposed mapping to change)
- Entry point / runner
  - `modules/job_runner/runtime.py` — runtime options (interactive_broker param) and paths to broker summary
  - `modules/job_runner/enhanced_daily_reports.py` — writer for final watchlist CSV + preview

- Broker ingestion / snapshots
  - `modules/broker_bridge/wait_for_broker_export.py` — current interactive export handling and detection
  - `modules/broker_bridge/broker_raw.py` — normalization (read-only)
  - `modules/database/swing_history_db.py` — broker_snapshots tables; ensure immutability

- Broker period/context & aggregation
  - `modules/broker_bridge/broker_period_context.py` — implement BrokerPeriodSpec, validation, snapshot root
  - `modules/data_sources/broker_history.py` — daily history storage
  - `modules/data_sources/broker_multiday_engine.py` — aggregation logic (3D/5D) — adapt to build aggregates from stored 1D only
  - `modules/data_sources/broker_multiday_output.py` — persist aggregate snapshot (no synthetic daily rows)

- Broker fusion / scoring / decision
  - DO NOT MODIFY scoring formula or Broker Confidence calculations.
  - Primary selection must be done by the Broker Bridge / orchestrator before Broker Fusion is invoked. The orchestrator determines PRIMARY context and passes it explicitly to Broker Fusion.
  - `modules/broker_fusion/broker_fusion.py` should accept an explicit PRIMARY input but must not change internal scoring formulas.
  - `modules/data_sources/decision_bridge.py` — attach broker context fields (`Broker_Context`, `Broker_Data_Date`, `Broker_Window`, `Broker_Snapshot_ID`) to decision rows.
  - `modules/decision_engine/*` (smart_selective_v162.py) — continue to consume Broker Score / Broker_Confidence as-is; ensure these are taken only from PRIMARY input.

- Reporting / UI
  - `modules/telegram/daily_report_ui.py` and `modules/telegram/telegram_bot.py` — add Broker Context / TODAY PULSE info and alignment metadata

- Tests and validation
  - `modules/job_runner/report_validation.py` — validate broker summary sources and new context
  - Add new tests under `tests/` for: aggregate building from 1D, incomplete history handling, alignment logic, provenance fields

Design constraints & rules (from user)
- Raw 1D source file must be treated as an immutable snapshot and fingerprinted (file hash + summary hash).
- Normalized daily history storage must NOT silently overwrite existing observations for the same (symbol, broker, date, side).
  - If the exact same capture reappears: deduplicate and reuse existing snapshot record.
  - If a different capture for the same trading date appears: either store as a revision with provenance metadata (preferred) or reject the import and surface an operator alert — do NOT overwrite without trace.
- Aggregate 3D/5D/CUSTOM must be computed only from stored real daily 1D rows (internal rolling recompute). Do NOT reconstruct aggregate by splitting aggregate exports into synthetic daily rows.
- If trading session(s) missing for the requested window, classify the window as `INCOMPLETE` with coverage metric (e.g., `2/3`) and list missing dates. Do NOT substitute other dates or shift the window.
- PRIMARY selection drives Broker Score / Broker Confidence. TODAY PULSE (latest 1D) is interpretation-only and MUST NOT change scoring, decision weights, entry, SL, TP1, TP2.
- Backward compatibility: existing snapshot files and database records MUST NOT be overwritten. New aggregate snapshots should be stored under `data/output/broker_snapshots/<trade_date>/<snapshot-id>/` and marked as derived (source=INTERNAL_ROLLUP or STOCKBIT_EXPORT).

IMPORTANT: immutability details
- Store snapshot metadata: `source_path`, `file_hash`, `summary_hash`, `provider`, `captured_at`, `committed_by_run_id`, `commit_status` (PENDING|COMMITTED|REJECTED).
- Only `COMMITTED` snapshots can be `REUSE`d by Final Watchlist; maintain validation that compares stored hash vs file hash before reuse.
Preserve existing Broker Bridge hardening
- Do not replace or weaken current protections already present in the bridge:
  - COMMITTED-only REUSE logic
  - snapshot hash validation (raw+summary)
  - global Broker Bridge lock and transaction semantics
  - rollback on failed Final Watchlist runs (do not leave partial commits)
  - strict IDX session validation (do not auto-fill missing sessions)
  - contract: aggregate != daily history (aggregates must not be injected into daily history table)

Internal rollup vs Stockbit aggregate
- Clarify provenance explicitly everywhere: INTERNAL_DAILY_ROLLUP vs STOCKBIT_AGGREGATE_EXPORT. Internal 3D/5D built from Top40 daily rows may differ from Stockbit's aggregate due to ranking/coverage differences. Always record `broker_period_source` = `INTERNAL_DAILY_ROLLUP` or `STOCKBIT_AGGREGATE_EXPORT` in outputs.

UI / REASON (formatter guidance)
- Before modifying any formatter, trace the active runtime Final Watchlist formatter and extend it. Do not add parallel or duplicate formatter paths.
- Preserve current interpretive Reason behaviour and extend it with: `broker_period_type`, `broker_period_source`, `broker_period_start`, `broker_period_end`, `broker_session_dates`, `today_pulse_date`, `today_pulse_snapshot_id`, `broker_alignment`.
- Reason should be human-readable and explicitly state whether PRIMARY came from INTERNAL rollup or STOCKBIT export and include coverage (e.g., `Coverage 2/3; missing: 2026-08-10`).

Next steps (implementation order)
1. Audit flow (this document)
2. Map functions to change (documented above)
3. Implement immutable daily 1D archive (DB + writer)
4. Implement internal rolling 3D/5D from real daily records
5. Implement CUSTOM internal builder
6. Implement fallback aggregate export handling when incomplete
7. Add PRIMARY selection handling and TODAY PULSE interpretation to final_watchlist runner
8. Implement alignment/divergence metadata generator
9. Update Final Watchlist Reason generator to include broker context provenance
10. Add unit tests + regression run

Checklist to report after implementation (what the final output must include)
- file list changed
- flow before/after (diagram + short text)
- tests added and test results
- requirement checklist PASS/FAIL
- remaining risks / TODOs

-- End of audit
