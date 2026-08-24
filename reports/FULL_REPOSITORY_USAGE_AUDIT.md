# SDE Swing Full Repository Usage Audit

Audit date: 2026-08-23 (Asia/Jakarta)  
Repository: Fikriafrizal99/sde-swing  
Required branch: testing  
Baseline snapshot: dfd86d4 — normalize row comparison hashes for sqlite numeric types  
Mode: READ-ONLY baseline plus execution addendum (ordered cleanup below)

## Audit boundary and method

The baseline audit started on branch testing with a clean worktree at 18b3af7.
During the audit, the local reflog recorded an external fast-forward pull to
dfd86d4. The inventory, AST analysis, test collection, test execution,
documentation review, and release validators were therefore repeated against
dfd86d4. No pull, checkout, reset, or commit was performed by the baseline
audit. The subsequent ordered execution is recorded as an addendum and remains
unstaged/uncommitted.

Baseline repository checks:

    git branch --show-current
    testing

    git status --short
    <empty; only inaccessible global-ignore warnings>

    git log -1 --oneline
    dfd86d4 normalize row comparison hashes for sqlite numeric types

Dependency evidence was collected from:

- all tracked Python imports and relative/package imports parsed with AST;
- package initializers, facade rebinding, globals/getattr/setattr, monkey patches,
  importlib, and literal module/path references;
- subprocess targets, Path/string targets, JSON path values, BAT/PowerShell
  launchers, Windows scheduler wrappers, and GitHub Actions;
- current tests, compatibility tests, current docs, historical docs, and Git
  history;
- tracked, untracked, ignored, runtime, cache, local environment, and user-data
  inventories;
- exact Git blob hashes for byte-identical duplicates;
- pytest collection and execution with bytecode/cache writes disabled;
- quant freeze, runtime-config, canonical-contract, and stabilization release
  validators.

The contents of secrets were not reported. For .env and ignored local JSON
configuration, only file metadata and key/variable names were inspected.

## 1. Executive Summary

### Counts

| Metric | Result | Definition |
|---|---:|---|
| Tracked files | 485 | Git index at dfd86d4 |
| Untracked files | 0 | git ls-files --others --exclude-standard |
| Ignored local files | 36,672 | One inaccessible .pytest_cache directory may make this a lower bound |
| Files on disk | 37,557 | Includes Git, environments, runtime data, and cache |
| Tracked bytes | 4,397,517 | Git tree payload |
| Total bytes on disk | 3,929,009,277 | About 3.93 GB decimal / 3.66 GiB |
| Executable/source files scanned | 335 | 295 Python, 33 BAT, 3 JS, 2 PS1, 2 workflow YML |
| Production/non-test source files | 222 | 182 Python plus 40 launcher/integration files |
| Active files | 207 | Combined ACTIVE, ACTIVE_WRAPPER, and ENGINE_LOCKED production source |
| Compatibility-only files | 5 | Retained compatibility APIs or deprecated call chains |
| Safe-delete candidates, HIGH | 1 | Tracked source with all required zero-dependency checks |
| Legacy-island candidates | 4 islands / 6 files | No current runtime root enters these groups |
| Dead-code symbol findings | 61 | 13 overwritten definitions plus 48 zero-static-reference symbols |
| Unused-import findings | 48 | Conservative AST candidates, reported separately from symbol count |
| Exact duplicate blobs | 0 | No two tracked files have the same Git blob |
| Stale/dead test findings | 19 across 14 files | 16 stale failing cases plus 3 collection-error files |
| Stale/contradictory docs | 5 | Active-facing documents, excluding explicitly historical evidence |
| Safe generated/local cleanup items | 465 | 464 pyc files plus one duplicate environment directory |
| Unknown source files | 3 | Manual intent cannot be established from repository callers |

The 222 production source files are exhaustively partitioned as follows:

    207 active / wrapper / engine-locked
      5 compatibility-only
      6 files in four legacy islands
      1 high-confidence safe-delete candidate
      3 unknown/manual-review files
    ------------------------------------------------
    222 production source files

### Inventory separation

| Category | Inventory | Audit treatment |
|---|---|---|
| SOURCE | 222 production executable files | Dependency and reachability audit |
| CONFIG | 8 tracked JSON configs; 3 relevant ignored local config/env files | Reader/writer/key audit; quant values frozen |
| TEST | 113 Python test files plus 22 tracked fixtures/support files | Collection, execution, and contract classification |
| DOC | 87 Markdown files plus tracked text/readme evidence | Current vs historical vs stale comparison |
| RUNTIME DATA | data/database, data/state, data/input, historical data | Never treated as source or automatic cleanup |
| GENERATED | data/output, logs, previews, charts, payload, scheduler output | Retention review, not source deletion |
| CACHE | 464 repository pyc files, .pytest_cache, .ua generated graph | Separate local cleanup |
| LOCAL ENVIRONMENT | .venv, .venv-1, .env, config/telegram.json, config/news.local.json, .claude | Preserve active environment/secrets; review duplicate environment only |

Tracked extension inventory:

| Extension | Count |
|---|---:|
| .py | 295 |
| .md | 87 |
| .bat | 33 |
| .json | 23 |
| .txt | 20 |
| .csv | 16 |
| .js | 3 |
| .ps1 | 2 |
| .yml | 2 |
| Other tracked files | 4 |

### Verification result

- Quant freeze: PASS; audited baseline
  121bc58b0f6a62dc3a844ee59575fe48ce86cc7d; MODERATE_BASELINE;
  auto_entry_enabled=false.
- Runtime config: PASS as VALID_WITH_WARNINGS.
- Canonical contracts: PASS; nine record types, multi-day context-only,
  protected decision columns untouched, twelve registered runtime jobs.
- Stabilization release gate: PASS; quant, lifecycle, replay, runtime status,
  canonical data; calibration SHADOW_ONLY.
- Pytest collection: 749 tests collected with three collection errors.
- Remaining suite after excluding the three collection-error modules:
  733 passed, 16 failed, 3 subtests passed.
- The two new incremental database archive tests pass.
- CI is currently red because normal CI runs the full pytest command without
  exclusions.

### Main conclusions

1. The preferred integrated runtime is present and internally consistent.
2. The database contract is current, not orphaned:

       config/pipeline.json paths.database_archiver
         -> modules/database/swing_history_db.py
         -> modules/database/market_price_archive_incremental.py

3. master_pipeline.py, telegram_bot.py, and swing_report_builder.py are not safe
   to delete. They remain a complete compatibility chain, but release validation
   now renders through the current professional UI adapter instead of calling
   telegram_bot.py directly.
4. The largest safe space cleanup is local, not source: .venv-1 plus repository
   bytecode saves about 129.27 MB.
5. The most certain internal dead code is thirteen earlier top-level definitions
   overwritten later in the same module.
6. The highest immediate engineering risk is a red CI suite whose failures mix
   three non-collecting scheduler tests with sixteen tests that still encode
   superseded launcher, report, configuration, or dependency behavior.

## 2. Active Runtime Map

### Control center

    RUN_SDE.bat
      -> maintenance/DAILY_OPERATIONS_MENU.bat
      -> maintenance/BROKER_MENU.bat
      -> maintenance/PORTFOLIO_MENU.bat
      -> maintenance/PERFORMANCE_MENU.bat
      -> maintenance/SYSTEM_MENU.bat
      -> maintenance/MAINTENANCE_MENU.bat

All 33 tracked BAT files are operational launchers, menu children, scheduler
launchers, or shared interpreter setup. No obsolete BAT file met the
high-confidence deletion threshold.

### Market Outlook

    RUN_MARKET_OUTLOOK.bat
      -> run_sde_job_integrated.py --job market_outlook
      -> run_sde_job.py
      -> job_market_outlook
      -> global_market_snapshot / Yahoo provider
      -> global market validation and scoring
      -> update_ihsg + market_outlook_regime + sector_rotation
      -> ZAPI metadata/activity enrichment
      -> enhanced_runtime_bridge
      -> EnhancedDailyReportBuilder
      -> modules/telegram/market_outlook_ui.py
      -> ReportPayload
      -> modules/job_runner/delivery.py
      -> TelegramRouter

Recovery and artifact access:

    RUN_MARKET_OUTLOOK.bat recovery
      -> tools/recover_market_outlook.py
      -> historical_global_market_snapshot
      -> batch range provider + validator + scoring
      -> prior-session IHSG/technical context + sector rotation
      -> enhanced market-outlook preview/status
      -> Telegram disabled

    preview/resend
      -> tools/resolve_last_trading_day.py
      -> tools/resend_daily_report.py
      -> existing artifacts only; no engine rerun

### Post Market

    RUN_POST_MARKET.bat
      -> run_sde_job_integrated_market_first.py
      -> monkey-patch integrated.post_market_payloads
         with post_market_live_payloads
      -> run_sde_job_integrated.py
      -> run_sde_job.py --job post_market
      -> job_post_market
      -> core.run_post_market_technical_stage
      -> config.paths.historical_downloader
      -> post_market_resilient_downloader.py
      -> DataSourceManager / canonical materialization
      -> post_market_validated_runner.py
      -> technical_feature_engine.py
      -> technical_candidate_selector.py
      -> technical snapshot/status
      -> outcome synchronization
      -> modules/job_runner/post_market_live.py
      -> modules/telegram/post_market_ui.py
      -> ReportPayload -> delivery.py -> TelegramRouter

Recovery uses the same market-first wrapper with an explicit resolved trade date
and --no-telegram. Preview/resend uses tools/resend_daily_report.py and existing
artifacts.

### Broker Summary

    maintenance/BROKER_MENU.bat
      -> run_sde_job_integrated.py --job broker_summary --no-telegram
      -> run_sde_job.py
      -> job_broker_summary
      -> broker export import/validation
      -> broker_navigator_export.py / wait_for_broker_export.py
      -> broker_fusion_publisher.py
      -> enhanced broker-summary payload
      -> local report/status because menu disables Telegram

### Broker Multi-Day

    maintenance/BROKER_MENU.bat
      -> run_sde_job_integrated.py --job broker_multi_day --no-telegram
      -> run_sde_job.py
      -> job_broker_multi_day
      -> core.run_broker_multiday_stage
      -> broker_history + broker_windows
      -> broker_multiday_engine + broker_multiday_output
      -> context-only artifact/report

The canonical validator confirms that this stage does not emit BUY/WATCH/AVOID
decisions.

### Final Watchlist

    RUN_FINAL_WATCHLIST.bat
      -> tools/run_final_watchlist_entrypoint.py
      -> resolve effective IDX trade date
      -> tools/run_final_watchlist_playwright_bridge.py
      -> tools/run_final_watchlist_broker_period.py
      -> run_sde_job.py stages
      -> broker summary + broker multi-day
      -> broker fusion
      -> Decision Engine
      -> Exit Engine
      -> config.paths.database_archiver
      -> swing_history_db.py
      -> market_price_archive_incremental.py for historical price rows
      -> enhanced final-watchlist builder/chart
      -> modules/telegram/final_watchlist_ui.py
      -> ReportPayload -> delivery.py -> TelegramRouter

Preview/resend is artifact-only through tools/resend_final_watchlist.py. The
approved compact formatter is current behavior and must not be changed merely
to satisfy old presentation tests.

### Full Daily and Full Manual

    maintenance/DAILY_OPERATIONS_MENU.bat
      -> tools/run_full_daily_broker_period.py
      -> Post Market through market-first integrated wrapper
      -> Market Outlook through integrated wrapper
      -> Final Watchlist through lifecycle entrypoint
      -> optional portfolio management

Official Full Manual:

    run_sde_job.py job_full_manual
      -> current ordered job handlers
      -> one final enhanced report assembly
      -> delivery.py

Compatibility Full Manual:

    run_sde_job.py job_full_manual when _official_runtime(ctx) is false
      -> core.run_master_pipeline
      -> master_pipeline.py
      -> configured Stage 1/2 subprocesses
      -> configured database archiver
      -> configured telegram_bot.py

This fallback proves that master_pipeline.py remains compatibility-only rather
than orphaned.

### Scheduler

    scheduler/Install-SdeSchedulers.ps1
      -> scheduler/SCHEDULE_MARKET_OUTLOOK.bat
         -> tools/run_scheduled_job.py -> integrated market outlook
      -> scheduler/SCHEDULE_POST_MARKET.bat
         -> tools/run_scheduled_job.py -> market-first post market
      -> scheduler/SCHEDULE_FINAL_WATCHLIST.bat
         -> tools/run_scheduled_job.py
         -> tools/run_final_watchlist_entrypoint.py --period 1D
      -> scheduler/SCHEDULE_IDX_DISCLOSURE.bat
         -> run_idx_disclosure_watcher.py --transport playwright

generate_task_scheduler_xml.py remains an active alternative invoked by
maintenance/GENERATE_SCHEDULER_XML.bat.

### Telegram maintenance

    maintenance/CONFIGURE_TELEGRAM_TOPICS.bat
      -> tools/telegram_settings.py
      -> set/validate credentials and topic routes

    maintenance/TEST_TELEGRAM.bat
      -> tools/telegram_settings.py validate-credentials
      -> tools/telegram_settings.py test-all

    maintenance/GET_CHAT_ID.bat
      -> modules/telegram/get_chat_id.py

These paths use the current router/settings boundary, not the compatibility
telegram_bot.py sender.

### News and IDX disclosure watcher

    RUN_MARKET_OUTLOOK.bat / RUN_POST_MARKET.bat
      -> modules/news/news_monitor_fresh_grouped.py
      -> news_monitor_market_impact.py / news_monitor.py
      -> dedicated NEWS route

    RUN_IDX_DISCLOSURE_WATCHER.bat
      -> run_idx_disclosure_watcher.py
      -> idx_disclosure browser/client fallback
      -> normalizer + repository
      -> official Telegram NEWS delivery
      -> durable AI queue
      -> local PDF extraction + Groq reader
      -> edit existing official message

The disclosure subsystem is isolated from trading engines. Its config keeps
decision_engine_write_access=false.

### Lifecycle, outcome, and portfolio operations

    maintenance/PERFORMANCE_MENU.bat
      -> modules/analytics/outcome_tracker.py
      -> sync/show/telegram/portfolio commands
      -> tools/send_lifecycle_digest.py
      -> tools/send_active_recommendations.py
      -> tools/preview_lifecycle_digest.py
      -> modules/analytics/exit_efficiency.py
      -> tools/broker_period_performance.py

Portfolio menus also call portfolio_broker_daily.py,
stockbit_playwright_collector.py, edit_position.py, manual_position_plan.py,
refresh_open_positions.py, position_management_runtime.py,
portfolio_delivery_status.py, and the provenance repair helper.

## 3. File Classification

Rows with explicit file names override broader glob rows. ACTIVE,
ACTIVE_WRAPPER, and ENGINE_LOCKED together form the 207 active-file count.

| File | Classification | Active Caller | Evidence | Risk | Recommendation |
|---|---|---|---|---|---|
| run_sde_job.py; run_idx_disclosure_watcher.py; generate_task_scheduler_xml.py; swing_utils.py | ACTIVE | BAT, scheduler, integrated runner, maintenance | Direct launcher imports/commands and CI/tests | HIGH if removed | Keep |
| run_sde_job_integrated.py; run_sde_job_integrated_market_first.py | ACTIVE_WRAPPER | Market/Post/Broker launchers and scheduler | Direct BAT/subprocess targets; market-first monkey patch is intentional | HIGH | Keep |
| modules/**/*.py except explicit rows below | ACTIVE | Current runtime, operator CLI, config path, package import, or current documentation | AST closure plus literal/config/launcher roots | Varies | Keep; audit symbols separately |
| modules/decision_engine/*.py; modules/technical_feature_engine/*.py; modules/candidate_selector/technical_candidate_selector.py; modules/broker_fusion/*.py; modules/exit_engine/*.py; modules/decision/*.py; modules/entry_plan_validator/*.py; protected analytics/profile/lifecycle files | ENGINE_LOCKED | Current engine stages and regression/freeze validators | Quant freeze and canonical contracts pass | CRITICAL | No change without separately approved engine audit |
| modules/database/swing_history_db.py; modules/backtesting/backtest_engine.py; modules/analytics/outcome_tracker.py; modules/analytics/profile_shadow.py; modules/job_runner/runtime.py | ACTIVE_WRAPPER | Config, runtime jobs, maintenance, compatibility callers | Facades delegate to baseline modules and preserve monkeypatch compatibility | HIGH | Keep facade/baseline pairs |
| modules/database/market_price_archive_incremental.py | ACTIVE | swing_history_db.py imports and rebinds archive_prices | Config -> facade -> incremental implementation; two current tests pass | HIGH | Keep |
| tools/*.py except explicit legacy/unknown rows | ACTIVE | BAT, scheduler, CI, current docs, or intentional current operator CLI | 30 tool files have direct roots or current operational purpose | MEDIUM/HIGH | Keep |
| All 33 tracked BAT; both scheduler PS1; both workflow YML | ACTIVE | Direct operator, menu, scheduler, or GitHub Actions entrypoints | Literal command map audited | HIGH | Keep |
| tampermonkey/Stockbit_Broker_Portfolio_Backfill_v2.user.js | ACTIVE | Portfolio backfill workflow | test_portfolio_broker_backfill.py specifically validates v2 namespace/output | MEDIUM | Keep |
| tampermonkey/Stockbit_Broker_Summary_Auto_Navigator_v3.1.user.js | ACTIVE | Broker navigator manual bridge | Broker symbol/export workflow and installation history | HIGH operational | Keep |
| master_pipeline.py | COMPATIBILITY_ONLY | run_sde_job.py non-official Full Manual branch | job_full_manual -> core.run_master_pipeline -> master_pipeline.py | HIGH if deleted | Migrate fallback before removal |
| modules/telegram/telegram_bot.py | COMPATIBILITY_ONLY | master_pipeline.py and explicit legacy Full Manual callers | config.paths.telegram_bot; release validation migrated to current professional UI | HIGH | Retain until Full Manual fallback is retired |
| modules/telegram/swing_report_builder.py | COMPATIBILITY_ONLY | telegram_bot.py and compatibility tests | Sibling import plus test_swing_v1_2.py | MEDIUM/HIGH | Migrate builder contracts before removal |
| modules/data_sources/manager.py | COMPATIBILITY_ONLY | External/public import compatibility | Explicit compatibility alias to runtime.data_source_manager; no internal caller | MEDIUM | Deprecate and search downstream consumers first |
| modules/data_preprocessor/stockbit_preprocessor.py | COMPATIBILITY_ONLY | Manual upstream preprocessing and two parse tests | No current pipeline caller; historical usage audit explicitly retained manual regeneration | MEDIUM | Confirm operator workflow before retirement |
| modules/data_sources/yahoo_zapi_validator.py | LEGACY_ISLAND_CANDIDATE | Tests only | Current runtime uses zapi_enrichment; source_validation config has no current reader | MEDIUM/HIGH | Migrate/remove stale reconciliation tests before deletion decision |
| modules/market_data/zapi_sector_metadata.py | DELETED / MIGRATED | test_zapi_sector_metadata.py | Test now exercises ZapiEnrichmentService | MEDIUM | Current owner is zapi_enrichment.py |
| modules/snapshots/__init__.py; modules/snapshots/builder.py | DELETED / MIGRATED | test_runtime_integration.py | Test now exercises modules/runtime/artifacts.py | MEDIUM | Current owner is runtime artifacts |
| tools/audit_quant_analysis.py; tools/trigger_backtest_v162.py | LEGACY_ISLAND_CANDIDATE | No current root | Hard-coded July 2026 audit snapshots/output chain; no launcher/config/current-doc/test caller | HIGH historical-reproducibility risk | Archive intent/evidence before any removal |
| tampermonkey/Stockbit_Broker_Portfolio_Backfill_v1.user.js | DELETED | None found | v2 is current/tested; exhaustive reference checks found v1 only in itself | LOW repository risk; external-install risk | Removed after explicit user instruction |
| prepare_sde_swing_audit.py | UNKNOWN_REVIEW_REQUIRED | Self-contained CLI only | No launcher/config/test/current-doc caller; docstring describes an audit ZIP utility | Unknown user workflow | Ask whether audit packaging remains required |
| tools/generate_manifest.py | UNKNOWN_REVIEW_REQUIRED | Self-contained CLI only | No current launcher; verify_manifest remains active; archived docs reference generator | Release-process ambiguity | Confirm release packaging policy |
| tools/test_idx_pdf_browser_fetch.py | UNKNOWN_REVIEW_REQUIRED | Self-contained diagnostic CLI | Recently repaired, but no launcher/config/test/current-doc caller | Diagnostic value unknown | Keep until IDX operator confirms |
| tests/** | TEST_ONLY | pytest/CI | 113 test modules and 22 fixture/support files | HIGH if engine contracts removed | Keep except approved migration of stale/dead contracts |
| config/audit_quant_freeze.json and protected quant portions of config/pipeline.json | ENGINE_LOCKED | CI validators and engines | Freeze validators PASS | CRITICAL | Never clean as unused config |
| Other tracked config/*.json | ACTIVE | Runtime/config loaders and operator tooling | Top-level readers confirmed; dormant leaf keys listed in Section 9 | HIGH | Keep files; review leaf keys only |
| docs/archive/v1.1/**; dated stabilization/stage reports; versioned reports/** | HISTORICAL_DOC | Audit/release evidence | Explicit version/date context, not runtime truth | Evidence loss | Keep or move only within documented archive policy |
| Current architecture/runtime/routing/data-source/database docs | ACTIVE | Operator and engineering contract | Match active entrypoint/config chains | MEDIUM | Keep current |
| Five stale/contradictory active-facing docs listed in Section 11 | UNKNOWN_REVIEW_REQUIRED | Human readers | Conflict with active source/runtime | Documentation risk | Correct or archive in Phase D |
| Six tracked data/broker backfill CSV/manifest files | ACTIVE | Historical/source evidence | Tracked intentional snapshots; not generated source code | USER/SOURCE DATA | Keep |
| data/output/**; logs/**; output/**; payload/**; generated .ua files | GENERATED_RUNTIME | Runtime or developer tools | Ignored by Git | Retention/storage | Treat separately; never as source deletion |
| .env; config/telegram.json; config/news.local.json | ACTIVE | Runtime credential/route/news configuration | Ignored and locally present; values not reported | SECRET | Keep local and untracked |
| .venv | GENERATED_RUNTIME | tools/set_python_cmd.bat | Explicit interpreter preference | Runtime dependency | Keep |
| .venv-1; repository __pycache__/*.pyc | GENERATED_RUNTIME | No repository caller | Ignored environment/cache; details in Section 12 | LOW after process check | Safe local cleanup after approval |
| Local databases, data/input, historical output, data/state, Playwright profile | UNKNOWN_REVIEW_REQUIRED | Runtime/user workflows | Active DB/history/browser state | CRITICAL data-loss risk | Do not delete |

## 4. SAFE_DELETE_CANDIDATE

### tampermonkey/Stockbit_Broker_Portfolio_Backfill_v1.user.js

**FILE:** tampermonkey/Stockbit_Broker_Portfolio_Backfill_v1.user.js  
**WHY UNUSED:** The repository contains and tests the v2 portfolio-backfill
userscript. V1 has its own obsolete v1 localStorage/IndexedDB namespace and no
repository entrypoint references it.  
**SEARCH EVIDENCE:** No active reference found after checking Python imports,
subprocess strings, config keys/values, BAT launchers, PowerShell scheduler
installation, GitHub workflows, tests, dynamic imports, current documentation,
and tracked text. Filename and v1 application identifiers occur only inside
the file itself.  
**CALLERS FOUND:** None.  
**CONFIG REFERENCES:** None.  
**TEST REFERENCES:** None. The active backfill test explicitly reads
Stockbit_Broker_Portfolio_Backfill_v2.user.js and validates its v2 database and
output contract.  
**DYNAMIC REFERENCE CHECK:** No JS loader, generated filename lookup, importlib,
Path target, or command string selects v1. Human installation outside the
repository remains possible and is the only residual uncertainty.  
**RISK:** Low for repository runtime; medium operationally if an operator still
uses the v1 installed userscript as a rollback copy.  
**DELETE CONFIDENCE:** HIGH, conditional on a one-time operator confirmation or
archival copy outside the active tampermonkey directory.

No other tracked source file reached HIGH confidence. In particular:

- prepare_sde_swing_audit.py and generate_manifest.py are self-contained manual
  CLIs whose human invocation cannot be disproved statically;
- tools/test_idx_pdf_browser_fetch.py was recently repaired and may be an
  intentional diagnostic;
- legacy-island modules still have tests or historical-reproducibility value;
- compatibility files have proven callers or public import contracts.

## 5. COMPATIBILITY_ONLY

### Full Manual and Telegram chain

    current official runtime
      -> run_sde_job.py job_full_manual
      -> current ordered handlers
      -> enhanced reports
      -> delivery.py

    compatibility branch
      -> run_sde_job.py job_full_manual
         when _official_runtime(ctx) is false
      -> modules/job_runner/core.py run_master_pipeline
      -> master_pipeline.py
      -> config/pipeline.json paths.telegram_bot
      -> modules/telegram/telegram_bot.py
      -> modules/telegram/swing_report_builder.py
      -> professional_ui / formatter compatibility helpers

Release validation (migrated):

    tools/validate_release.py
      -> tools/validate_current_telegram_presentation.py
      -> modules/telegram/professional_ui.py

**NOT SAFE TO DELETE.** Before removing this chain:

1. migrate or formally remove the non-official job_full_manual fallback;
2. prove parity for every configured Stage 1/2 subprocess and database/archive
   output used by compatibility runs;
3. replace direct Telegram dry-run validation (DONE: current professional UI
   presentation validator now renders the isolated release artifacts);
4. migrate tests that directly import swing_report_builder;
5. remove or migrate config paths backtest_engine and telegram_bot only after
   their last compatibility reader is gone;
6. run full CI, release validators, and a dry-run artifact/delivery comparison.

### DataSourceManager import alias

    potential old integration
      -> from modules.data_sources.manager import DataSourceManager
      -> modules/runtime/data_source_manager.py

There is no internal repository caller, but the file explicitly declares itself
a compatibility import and exposes a public __all__. Deprecation telemetry or a
downstream consumer search is required before deletion.

### Stockbit preprocessor

    historical/manual raw Stockbit CSV
      -> modules/data_preprocessor/stockbit_preprocessor.py
      -> normalized CSV

The current automated pipeline does not call it, but two tests protect its
numeric parser and the prior usage audit says it was retained for manual
upstream regeneration. Confirm that no operator still creates normalized input
with this CLI before migration or archival.

## 6. Legacy Islands

### Island A — Yahoo/ZAPI OHLCV reconciliation

    CURRENT RUNTIME
      -> ZAPI metadata/activity enrichment
      X no dependency into yahoo_zapi_validator.py

    config/scheduler.json (source_validation removed)
      -> no current reader

    modules/data_sources/yahoo_zapi_validator.py
      -> tests/test_full_manual_terminal_lifecycle.py
      -> tests/test_zapi_end_to_end.py

Current architecture states that Yahoo owns OHLCV and ZAPI does not validate or
supply DailyBar in production. The stale scheduler block was removed, but the
validator remains as a test/manual compatibility island because its
Yahoo-vs-ZAPI reconciliation event contract is still distinct from the current
enrichment service.

### Island B — old sector metadata refresher

    CURRENT RUNTIME
      -> modules/market_data/zapi_enrichment.py
      -> persistent metadata/activity cache
      X no dependency into zapi_sector_metadata.py

    modules/market_data/zapi_sector_metadata.py (deleted)
      -> tests/test_zapi_sector_metadata.py (migrated to ZapiEnrichmentService)

The test now exercises `ZapiEnrichmentService` envelopes, cache status, request
cap, and CSV output. The superseded module was deleted after that migration.

### Island C — snapshot facade

    CURRENT RUNTIME
      -> modules/runtime/artifacts.py
      -> engine-owned atomic artifacts
      X no dependency into modules/snapshots

    modules/snapshots/__init__.py; modules/snapshots/builder.py (deleted)
      -> tests/test_runtime_integration.py (migrated to write_artifact)

The test now verifies the current artifact owner, so the test-only facade and
builder were deleted.

### Island D — July 2026 quant audit/backtest

    CURRENT RUNTIME / CI
      X no dependency

    tools/audit_quant_analysis.py
      -> hard-coded 2026-07-27..31 snapshots and broker inputs
      -> data/output/audits/sde_swing_v162_analysis
      -> tools/trigger_backtest_v162.py
      -> historical audit CSV/JSON outputs

There is no launcher, config, current-doc, workflow, or test root. Because the
scripts encode historical analysis and import locked engines, preserve their
reproducibility intent before any archive/delete decision.

## 7. Dead Code Inside Active Files

### Certain overwritten definitions

These are high-confidence dead definitions because Python keeps only the last
top-level definition with the same name in a module. The earlier bodies are
unreachable after import. Thirteen earlier definitions occupy approximately
623 lines.

| File | Symbol | Caller Search | Dynamic Risk | Recommendation |
|---|---|---|---|---|
| modules/job_runner/enhanced_runtime_bridge.py | _artifact_payload at 204; winner at 1047 | Earlier definition overwritten in same module | NONE after module load | Remove earlier body only in approved internal cleanup |
| modules/job_runner/enhanced_runtime_bridge.py | _artifact_with_lineage at 224; winner at 1060 | Earlier definition overwritten | NONE | Same |
| modules/job_runner/enhanced_runtime_bridge.py | _builder at 246; winner at 1036 | Earlier definition overwritten | NONE | Same |
| modules/telegram/daily_report_ui.py | format_market_outlook at 184; winner at 859 | Earlier definition overwritten | NONE | Remove earlier body only after presentation regression review |
| modules/telegram/daily_report_ui.py | format_post_market at 328; winner at 914 | Earlier definition overwritten | NONE | Same |
| modules/telegram/daily_report_ui.py | format_broker_summary at 491; winner at 1015 | Earlier definition overwritten | NONE | Same |
| modules/telegram/daily_report_ui.py | format_broker_multiday at 506; winner at 1046 | Earlier definition overwritten | NONE | Same |
| modules/telegram/daily_report_ui.py | format_final_watchlist_summary at 549; winner at 1071 | Earlier definition overwritten | NONE | Same |
| modules/telegram/daily_report_ui.py | format_watchlist_detail at 668; winner at 1364 | Earlier definition overwritten | NONE | Same |
| modules/telegram/daily_report_ui.py | format_watchlist_detail at 1120; winner at 1364 | Earlier definition overwritten | NONE | Same |
| modules/telegram/professional_ui.py | momentum_state at 407; winner at 421 | Earlier definition overwritten | NONE | Remove earlier body after compatibility UI tests |
| modules/telegram/professional_ui.py | valid_plan at 917; winner at 937 | Earlier definition overwritten | NONE | Same |
| modules/telegram/professional_ui.py | entry_setup_lines at 982; winner at 1007 | Earlier definition overwritten | NONE | Same |

There is a second presentation indirection that is intentional and must not be
mistaken for dead code:

    modules/telegram/__init__.py
      -> imports daily_report_ui
      -> preserves old detail formatter as format_final_watchlist_detail
      -> rebinds market_outlook, post_market, and watchlist detail
         to the dedicated current formatter modules

enhanced_daily_reports imports from daily_report_ui after package initialization,
so it receives the dedicated current formatter functions.

### Zero-static-reference symbols

The following 48 top-level functions/classes have zero Name or Attribute
reference across all 295 tracked Python files, including tests. This pass is
more conservative than a call-only scan: type references, callbacks stored in
dispatch structures, imports, and ordinary attributes count as references.
String-based getattr, external imports, plugins, or human API consumers can
still exist, so public symbols are not high-confidence deletion targets.
Together they span at most 1,027 lines.

| File | Symbol(s) | Caller Search | Dynamic Risk | Recommendation |
|---|---|---|---|---|
| modules/analytics/outcome_tracker_baseline.py | _latest_price | Zero static reference | LOW; private, but engine baseline | Defer; engine/lifecycle frozen |
| modules/data_sources/broker_history.py | load_broker_window; enforce_retention | Zero static reference | MEDIUM public API | Deprecate/test before removal |
| modules/data_sources/broker_multiday_output.py | build_telegram_summary | Zero static reference | MEDIUM public report API | Verify no operator import |
| modules/data_sources/decision_bridge.py | run_shadow_comparison | Zero static reference | MEDIUM shadow API | Preserve until shadow workflow review |
| modules/database/swing_history_db_baseline.py | upsert_many; insert_many_if_missing | Zero static reference | HIGH external/schema compatibility | Do not touch in cleanup-only change |
| modules/exit_engine/exit_engine_baseline.py | nearest_resistance | Zero static reference | CRITICAL engine baseline | No change |
| modules/historical_downloader/historical_downloader.py | incremental_start_date | Zero static reference | MEDIUM public helper | Review API/tests |
| modules/job_runner/enhanced_runtime_bridge.py | validated_post_market_payloads; complete_daily_payloads | Zero static reference | MEDIUM report API | Deprecate before removal |
| modules/job_runner/report_validation.py | require_mapping_fields | Zero static reference | MEDIUM public validator | Review downstream use |
| modules/job_runner/reports.py | latest_date_from_csv; _global_market_lines; _snapshot_from_manifest; latest_run_manifest_for_job | Zero static reference | LOW for private, MEDIUM for public | Remove private helpers first after targeted tests |
| modules/job_runner/runtime_baseline.py | parse_hhmm | Zero static reference | MEDIUM baseline API | Keep until compatibility review |
| modules/market_data/zapi_enrichment.py | load_cached_enrichment | Zero static reference | MEDIUM public cache API | Confirm operator use |
| modules/portfolio/broker_portfolio_backfill.py | existing_broker_dates | Zero static reference | MEDIUM manual API | Review portfolio workflow |
| modules/portfolio/stockbit_playwright_collector.py | summary_from_payload | Zero static reference | MEDIUM public transform API | Review external/manual use |
| modules/runtime/jobs.py | IntegratedJobRunner | Zero static reference | HIGH public runtime API | Deprecate, do not immediately delete |
| modules/snapshots/builder.py | build_snapshot | Zero static reference | LOW inside legacy island | Migrate island test first |
| modules/telegram/daily_report_ui.py | _source_block | Zero static reference | LOW private helper | Candidate after current UI tests |
| modules/telegram/final_watchlist_ui.py | _actor_lines; _period_lines; _technical_status; _interpretive_reason | Zero static reference | LOW private, but approved report module | Remove only without changing active output |
| modules/telegram/formatters.py | format_money; clean_items; format_status_line | Zero static reference | MEDIUM public formatter API | Deprecate before removal |
| modules/telegram/professional_ui.py | broker_detail_lines; broker_party_summary; short_reason; format_closing_bell; format_position_evaluation; format_pipeline_status; format_exit_alert | Zero static reference | MEDIUM/HIGH compatibility presentation API | Coordinate with telegram compatibility migration |
| modules/telegram/swing_report_builder.py | build_pipeline_status; build_market_recap; build_watchlist_recap; build_performance_recap; build_exit_alerts; build_warning_messages | Zero static reference | HIGH compatibility API | Remove only with whole compatibility chain |
| swing_utils.py | clean_dataframe_for_json; read_csv_safely; csv_latest_date | Zero static reference | MEDIUM shared public utility | Deprecate and check downstream imports |
| tools/generate_telegram_ui_preview.py | latest_file | Zero static reference | LOW private-in-practice | Remove helper only, not current preview tool |
| tools/send_active_recommendations.py | _risk_reward | Zero static reference | LOW private | Candidate after targeted test |

### Execution audit — 48 zero-reference APIs (one by one)

The original scan grouped symbols by file for readability. The following ledger
expands all 48 symbols into individual decisions. Each item was checked against
the current tree after the duplicate-definition and legacy-island changes.

| # | Symbol | Decision | Rationale / follow-up |
|---:|---|---|---|
| 1 | `modules/analytics/outcome_tracker_baseline.py:_latest_price` | KEEP / FROZEN | Baseline engine helper; no cleanup mutation allowed. |
| 2 | `modules/data_sources/broker_history.py:load_broker_window` | KEEP / COMPAT | Public broker-history API; retain for manual/external callers. |
| 3 | `modules/data_sources/broker_history.py:enforce_retention` | KEEP / COMPAT | Public retention API; retain until an explicit deprecation window exists. |
| 4 | `modules/data_sources/broker_multiday_output.py:build_telegram_summary` | KEEP / COMPAT | Public report builder; operator usage is not statically discoverable. |
| 5 | `modules/data_sources/decision_bridge.py:run_shadow_comparison` | KEEP / SHADOW | Shadow comparison is an intentional safety boundary. |
| 6 | `modules/database/swing_history_db_baseline.py:upsert_many` | KEEP / SCHEMA | Baseline database contract; external/schema risk is high. |
| 7 | `modules/database/swing_history_db_baseline.py:insert_many_if_missing` | KEEP / SCHEMA | Baseline database contract; external/schema risk is high. |
| 8 | `modules/exit_engine/exit_engine_baseline.py:nearest_resistance` | KEEP / FROZEN | Exit-engine baseline helper; preserve exact behavior. |
| 9 | `modules/historical_downloader/historical_downloader.py:incremental_start_date` | KEEP / COMPAT | Deprecated calendar-overlap helper; retain for compatibility. |
| 10 | `modules/job_runner/enhanced_runtime_bridge.py:validated_post_market_payloads` | KEEP / ACTIVE | Canonical `post_market_live` now consumes it through an explicit compatibility alias. |
| 11 | `modules/job_runner/enhanced_runtime_bridge.py:complete_daily_payloads` | KEEP / REPORT API | Report payload API; keep while downstream/operator usage is reviewed. |
| 12 | `modules/job_runner/report_validation.py:require_mapping_fields` | KEEP / VALIDATOR | Public validation primitive; no safe deletion evidence. |
| 13 | `modules/job_runner/reports.py:latest_date_from_csv` | KEEP / SHARED | Generic report/date helper; retain for external/manual use. |
| 14 | `modules/job_runner/reports.py:_global_market_lines` | KEEP / REPORT | Private report helper; leave unchanged until report ownership is consolidated. |
| 15 | `modules/job_runner/reports.py:_snapshot_from_manifest` | KEEP / REPORT | Private manifest adapter; preserve artifact compatibility. |
| 16 | `modules/job_runner/reports.py:latest_run_manifest_for_job` | KEEP / REPORT API | Public manifest lookup; dynamic/manual callers remain possible. |
| 17 | `modules/job_runner/runtime_baseline.py:parse_hhmm` | KEEP / FROZEN | Runtime baseline helper; preserve behavior. |
| 18 | `modules/market_data/zapi_enrichment.py:load_cached_enrichment` | KEEP / CACHE API | Public cache reader; retain for operator/cache recovery workflows. |
| 19 | `modules/portfolio/broker_portfolio_backfill.py:existing_broker_dates` | KEEP / MANUAL COMPAT | Manual portfolio API; retain while v2/v3 workflows coexist. |
| 20 | `modules/portfolio/stockbit_playwright_collector.py:summary_from_payload` | KEEP / TRANSFORM COMPAT | Public payload transform; external/manual use is possible. |
| 21 | `modules/runtime/jobs.py:IntegratedJobRunner` | KEEP / RUNTIME API | Public runtime facade; deprecate only after caller migration. |
| 22 | `modules/snapshots/builder.py:build_snapshot` | MIGRATED / DELETED | Snapshot island moved to `modules.runtime.artifacts.write_artifact`; old module removed. |
| 23 | `modules/telegram/daily_report_ui.py:_source_block` | REMOVED | Private zero-reference helper; active formatters do not call it. |
| 24 | `modules/telegram/final_watchlist_ui.py:_actor_lines` | REMOVED | Superseded by compact actor formatter; output contract preserved. |
| 25 | `modules/telegram/final_watchlist_ui.py:_period_lines` | REMOVED | Superseded by current compact presentation; no active caller. |
| 26 | `modules/telegram/final_watchlist_ui.py:_technical_status` | REMOVED | Superseded by current compact presentation; no active caller. |
| 27 | `modules/telegram/final_watchlist_ui.py:_interpretive_reason` | REMOVED | Superseded by current compact action/reason contract; no active caller. |
| 28 | `modules/telegram/formatters.py:format_money` | KEEP / PUBLIC | Shared formatter API; compatibility imports remain possible. |
| 29 | `modules/telegram/formatters.py:clean_items` | KEEP / PUBLIC | Shared formatter API; compatibility imports remain possible. |
| 30 | `modules/telegram/formatters.py:format_status_line` | KEEP / PUBLIC | Shared formatter API; compatibility imports remain possible. |
| 31 | `modules/telegram/professional_ui.py:broker_detail_lines` | KEEP / COMPAT | Compatibility presentation API; migrate with the legacy Telegram chain. |
| 32 | `modules/telegram/professional_ui.py:broker_party_summary` | KEEP / COMPAT | Compatibility presentation API; migrate with the legacy Telegram chain. |
| 33 | `modules/telegram/professional_ui.py:short_reason` | KEEP / COMPAT | Compatibility presentation API; migrate with the legacy Telegram chain. |
| 34 | `modules/telegram/professional_ui.py:format_closing_bell` | KEEP / COMPAT | Legacy report contract still consumes this presentation surface. |
| 35 | `modules/telegram/professional_ui.py:format_position_evaluation` | KEEP / COMPAT | Legacy report contract still consumes this presentation surface. |
| 36 | `modules/telegram/professional_ui.py:format_pipeline_status` | KEEP / COMPAT | Legacy report contract still consumes this presentation surface. |
| 37 | `modules/telegram/professional_ui.py:format_exit_alert` | KEEP / COMPAT | Legacy report contract still consumes this presentation surface. |
| 38 | `modules/telegram/swing_report_builder.py:build_pipeline_status` | KEEP / COMPAT | Compatibility chain API; do not remove piecemeal. |
| 39 | `modules/telegram/swing_report_builder.py:build_market_recap` | KEEP / COMPAT | Compatibility chain API; do not remove piecemeal. |
| 40 | `modules/telegram/swing_report_builder.py:build_watchlist_recap` | KEEP / COMPAT | Compatibility chain API; do not remove piecemeal. |
| 41 | `modules/telegram/swing_report_builder.py:build_performance_recap` | KEEP / COMPAT | Compatibility chain API; do not remove piecemeal. |
| 42 | `modules/telegram/swing_report_builder.py:build_exit_alerts` | KEEP / COMPAT | Compatibility chain API; do not remove piecemeal. |
| 43 | `modules/telegram/swing_report_builder.py:build_warning_messages` | KEEP / COMPAT | Compatibility chain API; do not remove piecemeal. |
| 44 | `swing_utils.py:clean_dataframe_for_json` | KEEP / SHARED | Shared serialization utility; caller may be dynamic/external. |
| 45 | `swing_utils.py:read_csv_safely` | KEEP / SHARED | Shared IO utility; caller may be dynamic/external. |
| 46 | `swing_utils.py:csv_latest_date` | KEEP / SHARED | Shared date utility; caller may be dynamic/external. |
| 47 | `tools/generate_telegram_ui_preview.py:latest_file` | REMOVED | Private-in-practice helper with no caller; preview tool remains active. |
| 48 | `tools/send_active_recommendations.py:_risk_reward` | REMOVED | Private dead helper (and its private parser); no active caller. |

Summary: 8 symbols were removed/migrated safely; 40 remain intentionally
retained behind frozen, public, shared, shadow, or compatibility boundaries.
No zero-reference symbol was removed from the quant/decision/exit/database
baselines.

### Unused-import candidates

AST found 48 imported bindings with no local Name load. Imports inside
ENGINE_LOCKED or baseline files must remain untouched until a separately
approved engine audit. Imports used intentionally as dependency probes also
need manual confirmation.

| File | Unused imported binding(s) |
|---|---|
| master_pipeline.py | file_sha256 |
| modules/backtesting/backtest_engine_baseline.py | json |
| modules/broker_bridge/broker_navigator_export.py | json; os |
| modules/broker_bridge/wait_for_broker_export.py | json |
| modules/broker_fusion/broker_fusion.py | json; math |
| modules/data_sources/health.py | dataclasses.field |
| modules/data_sources/legacy_daily_bar_adapter.py | json |
| modules/data_sources/router.py | dataclasses.field |
| modules/database/swing_history_db_baseline.py | clean_dataframe_for_json |
| modules/decision_engine/decision_engine.py | math |
| modules/decision_engine/smart_selective_v162.py | math |
| modules/historical_downloader/historical_downloader.py | json; os |
| modules/idx_disclosure/ai_reader.py | pypdf at the dependency-probe site |
| modules/job_runner/core.py | datetime.date; datetime.datetime; load_data_source_config; stage_watchdog; BrokerPeriodSpec |
| modules/job_runner/delivery.py | json |
| modules/job_runner/post_market_live.py | json |
| modules/job_runner/report_validation.py | RunnerContext |
| modules/job_runner/reports.py | format_closing_bell; format_daily_signal_recap; format_post_market_summary; rank_watchlist |
| modules/market_data/market_outlook_regime.py | json |
| modules/market_data/sector_rotation.py | json |
| modules/market_data/zapi_enrichment.py | datetime.timedelta; ZapiIdxAdapter |
| modules/portfolio/portfolio_delivery_status.py | sys |
| modules/runtime/data_source_manager.py | BrokerFlow; ForeignFlow |
| modules/snapshots/builder.py | datetime.date; datetime.datetime |
| modules/technical_feature_engine/technical_feature_engine.py | json; datetime.datetime |
| modules/telegram/formatters.py | collections.abc.Iterable |
| modules/telegram/professional_ui.py | datetime.date |
| modules/telegram/swing_report_builder.py | shared_format_money |
| modules/telegram/telegram_bot.py | time |
| prepare_sde_swing_audit.py | os; typing.Iterable |
| run_sde_job.py | JOB_DEPENDENCIES |
| tools/recover_market_outlook.py | json |
| tools/trigger_backtest_v162.py | numpy as np |

Recommendation: remove imports in small, non-engine batches after targeted
tests. Do not combine import cleanup with compatibility migration or engine
changes.

## 8. Duplicate Implementations

No byte-for-byte duplicate tracked files exist. The repository does contain
semantic duplicates, wrappers, and superseded owners:

| Capability | Current Owner | Older Owner | Recommendation |
|---|---|---|---|
| Integrated orchestration | run_sde_job.py plus integrated wrappers | master_pipeline.py | Keep compatibility until fallback migration |
| Telegram delivery/routing | modules/job_runner/delivery.py plus modules/telegram/router.py | modules/telegram/telegram_bot.py | Release validation migrated; retain bot only for Full Manual/explicit compatibility callers |
| Enhanced report assembly | enhanced_daily_reports.py plus enhanced_runtime_bridge.py | swing_report_builder.py and legacy builders in reports.py/professional_ui.py | Remove only after compatibility tests migrate |
| Market Outlook presentation | market_outlook_ui.py | two overwritten bodies in daily_report_ui.py; professional_ui formatter | Current dedicated owner; clean overwritten bodies separately |
| Post Market presentation | post_market_ui.py and post_market_live.py | overwritten daily_report_ui bodies; professional_ui summary | Preserve approved current output |
| Final Watchlist presentation | final_watchlist_ui.py | two overwritten daily_report_ui bodies plus older professional UI helpers | Current dedicated owner; stale tests must migrate to compact contract |
| ZAPI metadata/activity | zapi_enrichment.py | yahoo_zapi_validator.py and zapi_sector_metadata.py | Treat older group as legacy islands |
| Runtime artifact publication | modules/runtime/artifacts.py | modules/snapshots/builder.py | Test-only facade migrated and deleted |
| Historical price archive | swing_history_db.py facade -> market_price_archive_incremental.py | swing_history_db_baseline.archive_prices | Intentional override, not a delete candidate; baseline owns schema/general archive |
| Facade/baseline implementations | outcome_tracker, profile_shadow, backtest_engine, exit_engine, runtime, swing_history_db | corresponding baseline modules | Intentional compatibility design; ENGINE_LOCKED where applicable |
| Portfolio browser backfill | Stockbit_Broker_Portfolio_Backfill_v2.user.js | v1 userscript | v1 is the only HIGH tracked safe-delete candidate |
| Trading-day/preview/recovery | resolve_last_trading_day plus artifact-only resend/recovery tools | old direct integrated-preview expectations in tests | Keep current path; migrate tests |

## 9. Config Audit

Eight tracked JSON configurations contain 869 leaf/value slots when arrays are
counted as configured values. The table audits them by reader-owned subtree and
then lists every leaf group for which no current runtime literal reader was
found. A missing literal reader does not prove safe deletion when a whole
subtree is loaded generically or protected by a validator.

| Config Key | Reader | Status | Recommendation |
|---|---|---|---|
| audit_quant_freeze.json.* | ci_validate_quant_freeze.py; stabilization release validator; quant tests | ACTIVE_CONFIG_KEY | ENGINE_LOCKED; keep all evidence/hash fields |
| pipeline.paths.historical_downloader, ihsg_updater, technical_feature_engine, candidate_selector, broker_navigator_export, broker_bridge, broker_fusion, decision_engine, exit_engine, database_archiver | core.py and/or master_pipeline.py subprocess construction | ACTIVE_CONFIG_KEY | Keep |
| pipeline.paths.database_archiver | core.py and master_pipeline.py | ACTIVE_CONFIG_KEY | Keep swing_history_db.py facade path |
| pipeline.paths.backtest_engine; pipeline.paths.telegram_bot | master_pipeline.py / explicit legacy Full Manual only | COMPATIBILITY_CONFIG_KEY | Remove only after the remaining compatibility caller is retired |
| pipeline.paths.profile_shadow_runner and output root | core.py and master_pipeline.py | ACTIVE_CONFIG_KEY | Keep |
| pipeline data_freshness acquisition/retry/market-close/holiday fields | core.py, historical downloader, runtime config validation | ACTIVE_CONFIG_KEY | Keep |
| pipeline.data_freshness.yahoo_refresh_mode; technical_refresh_mode; calendar_source | No current runtime literal reader | UNUSED_CONFIG_KEY | Document as declarative or remove only after schema approval |
| pipeline.zapi.enabled, metadata_ttl_days, max_requests_per_process, max_retries, minimum_historical_candles | core.py and zapi_enrichment.py | ACTIVE_CONFIG_KEY | Keep |
| pipeline.zapi.market_activity_cache_days | No current runtime literal reader | UNUSED_CONFIG_KEY | Review cache policy implementation |
| pipeline.broker.minimum_multiday_sessions | No current runtime literal reader | UNUSED_CONFIG_KEY | Do not confuse with actual window/session contracts |
| pipeline.exit.min_rr, preferred_rr, max_risk_pct, max_hold_days | core.py/master_pipeline.py -> Exit Engine CLI | ACTIVE_CONFIG_KEY | ENGINE_LOCKED |
| pipeline.exit.bear_market_policy; thin_liquidity_policy; resistance_must_be_above_entry | No current runtime literal reader | UNUSED_CONFIG_KEY | Quant/exit semantics frozen; review, do not delete in cleanup |
| pipeline.decision.weights, profiles, setup_profiles, hard_blockers, statuses, production_profile, calibration.mode, auto_entry_enabled | Decision engines and quant freeze validators | ACTIVE_CONFIG_KEY | ENGINE_LOCKED |
| pipeline.decision.calibration.preferred_shadow_sessions; selection_metric | No current runtime literal reader, but protected policy subtree | UNUSED_CONFIG_KEY | Preserve until approved quant-governance review |
| pipeline.decision.portfolio.risk_per_trade_pct | No current runtime literal reader | UNUSED_CONFIG_KEY | Portfolio-risk semantics frozen; no cleanup change |
| pipeline.decision.microstructure.buy_ready_required_metrics; missing_data_class | No current runtime literal reader | UNUSED_CONFIG_KEY | Treat as dormant policy, not safe delete |
| pipeline.runtime.configuration_policy; allow_legacy_override; write_config_audit | No current runtime literal reader | UNUSED_CONFIG_KEY | Either enforce in runtime or remove in a config-schema change |
| scheduler.timezone, trading_calendar, data_sources_config, paths.preview_root/job_status_root/state_root/job_log | Runtime context and scheduler wrapper | ACTIVE_CONFIG_KEY | Keep |
| scheduler.trading_days_only; runner_entrypoint | Scheduler wrapper hard-codes canonical commands and does not read these keys | UNUSED_CONFIG_KEY | Remove or wire only in dedicated scheduler cleanup |
| scheduler.output_roots.job_status/snapshots/broker_snapshots/decisions/previews/reports | Runtime/artifact loaders | ACTIVE_CONFIG_KEY | Keep |
| scheduler.output_roots.market_snapshots; technical_snapshots; broker_multi_day_snapshots; watchlists; audits | No exact current reader | UNUSED_CONFIG_KEY | Review path schema |
| scheduler.source_validation.* | No current runtime root; old yahoo_zapi_validator island reflects portions of this contract | LEGACY_CONFIG_KEY | Migrate/remove with legacy Island A |
| scheduler.enhanced_reporting.enabled/output_root/max_watchlist_messages and used reporting fields | Integrated report builders | ACTIVE_CONFIG_KEY | Keep |
| scheduler.enhanced_reporting.send_final_watchlist_csv; ai_interpretation.fallback_to_deterministic; immutable_engine_fields; max_watchlist_calls | No exact current reader or behavior now owned elsewhere | UNUSED_CONFIG_KEY | Reconcile with enhanced report implementation |
| scheduler.market_outlook, post_market time/fallback, news_monitor, locks, broker_readiness, scheduler_runtime, delivery | Job runner/scheduler/delivery/news modules | ACTIVE_CONFIG_KEY | Keep |
| scheduler.post_market.allow_broker_fusion; allow_final_decision; report_mode; market_heatmap.color_by/size_by | No exact current runtime reader | UNUSED_CONFIG_KEY | Document or prune after report/heatmap review |
| scheduler.final_watchlist.strict_trade_date_match; detail_statuses | No exact current runtime reader | UNUSED_CONFIG_KEY | Current final-watchlist logic has explicit contracts; reconcile |
| scheduler.runtime.job_lock_enabled; stale_lock_detection | No exact current runtime reader; global_resource_lock_enabled is used | UNUSED_CONFIG_KEY | Reconcile lock schema |
| global_market.json.* | global market registry/provider/snapshot/validator/scoring; recovery uses lookback | ACTIVE_CONFIG_KEY | Keep; instrument arrays are read generically |
| data_sources.json.record_ownership and sources.* | modules/data_sources/config.py and DataSourceManager | ACTIVE_CONFIG_KEY | Keep; whole dictionaries are mapped generically |
| data_sources.conflict_defaults.numeric_tolerance_pct | Conflict resolution/data-source config | ACTIVE_CONFIG_KEY | Keep |
| data_sources.conflict_defaults.suspend_conflict_policy; check_corporate_action_before_ohlc_conflict | No exact runtime reader | UNUSED_CONFIG_KEY | Review against conflict resolver before removal |
| idx_disclosure request/polling/state path/delivery essentials/ai_reader operational fields | run_idx_disclosure_watcher.py and idx_disclosure modules | ACTIVE_CONFIG_KEY | Keep |
| idx_disclosure.state.first_run_mode; dry_run_new_records | No exact runtime reader | UNUSED_CONFIG_KEY | Either enforce stated seed behavior or remove declaration |
| idx_disclosure.delivery.topic_route; reuse_existing_news_topic; require_news_topic; direct_idx_attachment_links | Current delivery derives routing elsewhere; no exact reader | UNUSED_CONFIG_KEY | Reconcile docs/config/runtime |
| idx_disclosure.ai_reader.delivery_mode; cost_controls.*; nested safety declarations | No direct leaf readers; docs/governance describe behavior | UNUSED_CONFIG_KEY | Treat as declarative guardrails until schema decision |
| idx_disclosure.safety.* | Tests/docs/governance; runtime primarily uses ai_reader.enabled and CLI conditions | UNUSED_CONFIG_KEY | Do not remove safety claims without replacing enforcement |
| idx_fca_exclusions.json.* | tools/update_idx_universe.py manual operator tool | ACTIVE_CONFIG_KEY | Keep as source/governance data |
| trading_calendar.json.* | trading-day resolver, runtime context, recovery/final entrypoint | ACTIVE_CONFIG_KEY | Keep |
| Local config/telegram.json | delivery.py, router/settings, legacy bot | ACTIVE_CONFIG_KEY | Keep ignored; never commit credentials |
| Local .env variables | ZAPI, Stockbit, Brave, Groq, Telegram consumers | ACTIVE_CONFIG_KEY | Keep ignored; values not included in audit |
| Local config/news.local.json brave_search_api_key | News tooling fallback/local config | ACTIVE_CONFIG_KEY | Keep ignored |
| .env.example | No tracked file exists, while docs/CONFIGURATION.md says it is blank | UNUSED_CONFIG_KEY / documentation gap | Either add an intentionally blank template in a separate approved change or correct the doc |

Dependency declaration finding:

- requirements.txt now includes playwright>=1.54 because the active IDX watcher
  browser path needs it.
- requirements-playwright.txt also declares playwright>=1.49,<2.0 and still says
  it is only an optional local browser collector.
- The constraints are compatible, but the duplicated ownership and comment are
  stale. Consolidate or clearly separate core IDX and optional portfolio-browser
  installation semantics in Phase D/E.

## 10. Test Audit

Commands and results:

    python -m pytest --collect-only -q -p no:cacheprovider
    749 tests collected, 3 collection errors

    python -m pytest -q -p no:cacheprovider
      --ignore=<three collection-error files>
    733 passed, 16 failed, 3 subtests passed

| Test | Contract | Status | Recommendation |
|---|---|---|---|
| 99 collecting test files with no failing case, plus passing cases in the mixed files | Engine, canonical data, runtime, archive, lifecycle, scheduler, delivery, presentation | KEEP | Retain |
| tests/test_incremental_market_price_archive.py | Current row-incremental archive and correction/idempotency behavior | KEEP | Retain; both new tests pass |
| tests/test_audit_quant_freeze.py and engine/canonical suites | Frozen deterministic engine and source ownership | KEEP | Never delete for cleanup |
| tests/test_swing_v1_2.py portions importing swing_report_builder; master-pipeline ownership tests | Compatibility behavior | COMPATIBILITY_TEST | Keep until compatibility migration |
| tests/test_task_scheduler_xml_encoding_v2.py | Old TASKS constant/build_task API | DEAD_TEST | Migrate to TaskSpec/task_specs API introduced by ed12feb; do not merely delete |
| tests/test_task_scheduler_xml_import_hotfix.py | Old TASKS constant/build_task API | DEAD_TEST | Same |
| tests/test_v161_report_and_bat_structure.py | Old scheduler API plus mixed v1.6 contracts | DEAD_TEST | Split still-valid contracts and migrate scheduler imports |
| test_broker_period_flow_continuation.py: final-watchlist primary labels | Verbose Primary 3D text | STALE_TEST_CANDIDATE | Update to approved compact final-watchlist facts |
| test_broker_period_primary_pulse.py: final-watchlist pulse text | Verbose TODAY PULSE text | STALE_TEST_CANDIDATE | Assert preserved facts/artifacts, not removed headings |
| test_enhanced_daily_reports.py: final-watchlist format | Old bold multi-section HTML card | STALE_TEST_CANDIDATE | Update expected approved compact format |
| test_final_watchlist_interpretive_reason.py, three cases | Old Reason heading and verbose level labels | STALE_TEST_CANDIDATE | Update to compact action sentence contract |
| test_final_watchlist_presentation.py, three cases | Old bold header, TP labels, separator | STALE_TEST_CANDIDATE | Update expected current caption; keep tick-rounding behavior assertions |
| test_v170_telegram_contract.py, one case | Old Reason heading | STALE_TEST_CANDIDATE | Preserve escaping/fact assertions, update presentation string |
| test_zapi_end_to_end.py, one case | Old Broker Summary heading | STALE_TEST_CANDIDATE | Preserve immutable ZAPI facts, update UI expectation |
| test_control_center_launcher.py, one case | Assumes every submenu calls run_sde_job_integrated.py directly | STALE_TEST_CANDIDATE | Accept market-first Post Market and lifecycle Final Watchlist wrappers |
| test_final_watchlist_entrypoint.py, two cases | Assumes scheduler/direct preview enters integrated engine | STALE_TEST_CANDIDATE | Assert run_scheduled_job retry wrapper and artifact-only resend path |
| test_idx_disclosure_architecture.py, one case | Expects top safety.ai_enabled=false | STALE_TEST_CANDIDATE | Align with current documented optional AI reader while retaining isolation/safety assertions |
| test_stockbit_playwright_collector.py, one case | Expects Playwright absent from core requirements | STALE_TEST_CANDIDATE | Account for active IDX Playwright dependency; keep collector isolation assertions |

The sixteen failures are distributed over eleven files. The compact
Final Watchlist change is traceable to commit 0c40616; scheduler API hardening
to ed12feb; IDX AI enablement to current config/docs; and core Playwright
addition to the IDX browser fallback work. This evidence supports stale-test
classification, but the test changes still require explicit approval because
the active report format is frozen.

## 11. Documentation Audit

| Document | Status | Recommendation |
|---|---|---|
| README.md | CURRENT | Keep; main is documented as release source of truth while this audit correctly targets testing |
| docs/README.md | CURRENT | Keep |
| docs/ARCHITECTURE.md | CURRENT | Keep; matches integrated reporting, compatibility, and incremental archive boundaries |
| docs/RUNTIME_JOBS.md | CURRENT | Keep; recovery and artifact-only preview/resend match launchers |
| docs/TELEGRAM_ROUTING.md | CURRENT | Keep; accurately marks delivery.py primary and legacy bot/builder compatibility-only |
| docs/DATABASE_ARCHIVE.md | CURRENT | Keep; facade and incremental archive contract match code/tests |
| docs/DATA_SOURCES.md | CURRENT | Keep |
| docs/SCHEDULER_RELIABILITY.md and docs/TROUBLESHOOTING.md | CURRENT | Keep |
| docs/IDX_AI_DOCUMENT_READER.md | CURRENT | Keep; reflects Playwright -> official delivery -> queued AI/edit flow |
| docs/IDX_DISCLOSURE_WATCHER_ARCHITECTURE.md | CURRENT with historical HTTP guidance | Clarify that Playwright is now the configured proven fallback/primary transport |
| docs/CONFIGURATION.md | CONTRADICTORY | It says .env.example is blank, but no .env.example is tracked; correct doc or add approved blank template |
| docs/archive/v1.7/PIPELINE_VALIDATION_REPORT.md | ARCHIVED | Historical snapshot banner added; it audits removed START_SDE_SWING.bat and old 265/273-test results |
| modules/backtesting/README.md | STALE | Replace claim that master_pipeline.py is the official path with current integrated/compatibility wording |
| modules/technical_feature_engine/README.md | STALE | Same |
| modules/candidate_selector/README.md | CONTRADICTORY | RUN_SDE remains valid, but master_pipeline.py is no longer an equal official path |
| modules/exit_engine/README.md | CURRENT ENGINE DOC | Keep; do not alter engine semantics |
| docs/archive/v1.7/SDE_AUDIT_BASELINE.md | HISTORICAL_ARCHIVE / GOVERNANCE | Kept as dated audit baseline; not presented as current guidance |
| docs/archive/v1.7/SDE_STABILIZATION_BASELINE.md | HISTORICAL_ARCHIVE / GOVERNANCE | Kept; old failure/pass counts are explicitly baseline evidence |
| docs/archive/v1.7/SDE_STABILIZATION_AUDIT_TRACEABILITY.md | HISTORICAL_ARCHIVE / GOVERNANCE | Kept; prior closure counts are dated evidence |
| docs/archive/v1.7/SDE_STABILIZATION_DEFERRED_FINDINGS.md | HISTORICAL_ARCHIVE / BACKLOG | Kept for traceability; unresolved entries require separate review |
| 39 Markdown files under docs/archive/v1.1 | HISTORICAL_ARCHIVE | Keep; no automatic deletion |
| 20 SDE_/STAGE dated evidence documents under docs/archive/v1.7 | HISTORICAL_ARCHIVE | Organized under the V1.7 archive index |
| reports/FILE_USAGE_AUDIT.md/json and versioned release/E2E/Telegram reports | HISTORICAL_ARCHIVE | Keep as prior-release evidence; do not use as current architecture truth |
| docs/LEGACY_FILE_MANIFEST.md | CURRENT | Updated with the archived-documentation class |

Stale/contradictory active-facing document count after archiving: four
(CONFIGURATION and three module READMEs).
Historical documentation is intentionally excluded from that count.

## 12. Local/Generated Cleanup

### High-confidence local cleanup

| Local item | Files | Bytes | Local class | Evidence / Recommendation |
|---|---:|---:|---|---|
| .venv-1 | 5,341 | 120,662,305 | SAFE_LOCAL_CLEANUP | Same Python 3.13 base as .venv; zero repository reference; .venv is the launcher-selected environment. Confirm no open process, then remove only after approval |
| Repository pyc files outside both environments | 464 | 8,606,299 | SAFE_LOCAL_CLEANUP | Ignored generated bytecode; 14 are sourceless remnants |

Total high-confidence local cleanup: 465 items and about 129.27 MB.

Sourceless bytecode proves earlier removed/renamed modules and tests, but it does
not restore a source dependency:

- modules/analytics/trade_lifecycle.py;
- modules/data_sources/broker_period.py;
- modules/data_sources/yahoo_adapter.py;
- modules/data_sources/yahoo_provider.py;
- modules/exit_engine/target_engine.py;
- tests/test_broker_multiday_selected.py;
- tests/test_broker_period_selection.py;
- tests/test_idx_ai_background_worker.py;
- tests/test_target_engine_enhancement.py;
- tests/test_telegram_enhancement.py;
- tests/test_trade_lifecycle_enhancement.py;
- tests/test_trade_management_persistence.py;
- tests/test_yahoo_canonical_contract.py;
- tests/test_yahoo_provider.py.

### Generated/runtime data requiring retention policy

| Path/category | Files | Bytes | Local class | Recommendation |
|---|---:|---:|---|---|
| data/output | 17,201 | 666,499,780 | RUNTIME_REQUIRED / GENERATED | Apply date/job retention policy; do not bulk-delete |
| data/output/historical | 14,240 | 505,876,342 | SOURCE_DATA / RUNTIME_REQUIRED | Preserve; provider repair/history input |
| data/output/snapshots | 211 | 63,516,930 | RUNTIME_REQUIRED | Preserve audit/replay window |
| data/output/manifests | 786 | 45,858,942 | RUNTIME_REQUIRED | Preserve lineage window |
| data/output/previews | 731 | 842,240 | GENERATED | Eligible for age-based cleanup after delivery/audit retention |
| output/final_watchlist | 24 | 3,140,953 | GENERATED | Charts/previews; age-based cleanup only |
| logs plus nested log files | 16 | 6,361,153 | RUNTIME_REQUIRED / GENERATED | Rotate/retain; do not blindly delete current evidence |
| payload | 10 | 236,638 | UNKNOWN | Confirm release/external ownership before cleanup |
| .ua generated graph/cache | 5 generated files plus tracked ignore config | GENERATED | Rebuildable, but preserve if current code-understanding session needs it |
| .pytest_cache | Access denied; reported as zero visible files | UNKNOWN | Do not force-delete; fix ownership/permission separately if desired |
| 22 browser-profile LOG.old files | 22 | 3,775 | USER_DATA / browser state | Do not treat suffix alone as safe; keep with Playwright profile |

### Explicitly not cleanup targets

| Path/category | Files | Bytes | Local class | Reason |
|---|---:|---:|---|---|
| data/database | 3 | 2,604,953,600 | RUNTIME_REQUIRED | Primary history DB, audit snapshot, broker multi-day DB |
| Other local SQLite/DB state | 6 | about 1.67 MB | RUNTIME_REQUIRED / USER_DATA | IDX disclosure, Telegram idempotency, browser state |
| data/state | 2,719 | 141,807,921 | RUNTIME_REQUIRED / USER_DATA | Scheduler, idempotency, queue, browser authentication/profile |
| data/input | 56 | 9,068,588 | SOURCE_DATA / USER_DATA | Current/manual broker and market inputs |
| data/archive | 16 | 9,327 | USER_DATA / HISTORICAL | Portfolio broker provenance |
| .venv | 10,803 | 347,588,786 | LOCAL ENVIRONMENT | tools/set_python_cmd.bat explicitly selects it |
| .env | 1 | 1,403 | LOCAL ENVIRONMENT / SECRET | Ignored; required credential names present |
| config/telegram.json | 1 | 1,738 | LOCAL ENVIRONMENT / SECRET/ROUTING | Ignored; active readers |
| config/news.local.json | 1 | 65 | LOCAL ENVIRONMENT / SECRET | Ignored; local Brave key |
| .claude/settings.local.* | 1 | 77 | LOCAL ENVIRONMENT | Ignored editor/agent settings |

No database, historical data, broker input, scheduler state, browser profile,
or local secret was deleted or modified.

## 13. Proposed Cleanup Plan

The original audit recorded the plan before execution. The ordered execution
status is now tracked below; the detailed one-by-one API ledger is in the
preceding section.

### Ordered execution status

| Order | Workstream | Status | Evidence |
|---:|---|---|---|
| 1 | Hijaukan CI | DONE | Full pytest and the four repository validators are rerun at hand-off. Offline E2E release validation was also attempted but stops earlier on an existing V2→V3 decision-contract mismatch. |
| 2 | Bersihkan `.venv-1` + `__pycache__`/pyc | DONE | `.venv-1` removed; repository caches/pyc removed after final validation. |
| 3 | Hapus Portfolio Backfill v1 | DONE | `tampermonkey/Stockbit_Broker_Portfolio_Backfill_v1.user.js` removed. |
| 4 | Bersihkan 13 overwritten definitions | DONE | Runtime/report/UI winners are unique; stale bodies removed. |
| 5 | Audit 48 zero-reference APIs satu per satu | DONE | Individual ledger records all 48 decisions and preserves protected APIs. |
| 6 | Migrasikan legacy islands | DONE / BOUNDED | Snapshot and ZAPI sector metadata islands moved to current runtime owners; historical validator/tools retained as evidence. |
| 7 | Migrasikan compatibility chain | DONE / BOUNDED | Release validation now renders through `tools/validate_current_telegram_presentation.py`/`professional_ui` (manual artifact check: 15 reports); deprecated `master_pipeline.py` → `telegram_bot.py` remains only for explicit legacy/regression callers. |
| 8 | Optional structural refactor | SKIPPED | No additional structural rewrite was needed after contract validation. |

### PHASE A — SAFE CLEANUP

1. Confirm that no operator needs the installed v1 Portfolio Backfill userscript
   as a rollback source; archive it outside the active source tree if desired.
2. Delete only
   tampermonkey/Stockbit_Broker_Portfolio_Backfill_v1.user.js.
3. Separately remove local .venv-1 after verifying no running process or manual
   shortcut uses it.
4. Remove repository __pycache__ directories/pyc files.
5. Re-run status, current targeted tests, full pytest, and validators.

Expected immediate reduction:

- tracked source: 1 file, 612 lines, 23,825 bytes;
- local disk: about 129.27 MB;
- source-line reduction: about 0.87% of the 70,427 production source lines.

### PHASE B — COMPATIBILITY MIGRATION

1. Decide whether non-official Full Manual remains supported.
2. Migrate/remove its master_pipeline fallback only with artifact parity.
3. Replace validate_release.py's telegram_bot dry-run with current delivery
   validation.
4. Migrate swing_report_builder tests/contracts.
5. Deprecate modules.data_sources.manager and confirm no downstream import.
6. Confirm the manual stockbit_preprocessor workflow.
7. Only then remove obsolete compatibility config keys/files.

Compatibility files represent 2,140 lines. This is not an immediate deletion
estimate because replacement/migration code may offset the reduction.

### PHASE C — INTERNAL DEAD CODE

1. Remove the thirteen overwritten definitions in isolated presentation/runtime
   cleanup commits. Their approximate span is 623 lines.
2. Start with low-dynamic-risk private zero-reference helpers.
3. Add deprecation or targeted tests for public zero-reference APIs.
4. Remove unused imports in non-engine modules in small batches.
5. Do not edit ENGINE_LOCKED files or quant config merely for cleanup.

If every zero-reference candidate were independently proven dead, its upper
bound is another 1,027 lines. That number is a review ceiling, not an approved
deletion amount.

### PHASE D — DOC/TEST CLEANUP

1. Migrate the three scheduler tests from TASKS to TaskSpec/task_specs.
2. Update sixteen stale test expectations to the approved compact report,
   current launcher wrappers, current IDX AI configuration, and current
   Playwright dependency boundary.
3. Keep engine/freeze/canonical assertions unchanged.
4. Correct the four remaining stale/contradictory active-facing docs.
5. Keep historical pipeline validation evidence under the dated archive.
6. Reconcile .env.example policy and dependency comments.
7. Require normal CI to return green before source cleanup merges.

### PHASE E — OPTIONAL REFACTOR

Only after Phases A-D and separate approval:

- consolidate report/presentation ownership without changing approved output;
- simplify facade/baseline structure where compatibility is formally retired;
- remove legacy islands after test and historical-evidence migration;
- normalize scheduler/config schemas and add reader coverage for declarative
  safety/policy keys;
- introduce age-based runtime artifact/log retention tooling.

### Complexity estimate

| Scope | Potential reduction | Confidence |
|---|---:|---|
| Approved-candidate source only | 612 lines / 1 file | HIGH after operator confirmation |
| Certain overwritten definitions | 623 lines / 13 definitions | HIGH semantically; regression approval still required |
| Zero-static-reference symbols | Up to 1,027 lines / 48 symbols | MEDIUM/LOW until dynamic/public API review |
| Four legacy islands | 1,502 lines / 6 files | MEDIUM after test/evidence migration |
| Five compatibility files | 2,140 lines / 5 files | LOW until caller migration |
| Eventual theoretical upper bound | About 5,904 lines, 8.38% of production LOC | Not an immediate plan; migrations may reduce net savings |
| Safe local disk cleanup | About 129.27 MB, roughly 3.3% of current repository disk footprint | HIGH after process check |

Recommended order:

    1. Restore green CI contracts
    2. Execute approved local cache/duplicate-environment cleanup
    3. Remove the one HIGH tracked candidate
    4. Remove certain overwritten definitions
    5. Review zero-reference public APIs
    6. Migrate legacy islands
    7. Migrate compatibility chain
    8. Consider optional structural refactor

Highest-risk mistake to avoid:

    deleting master_pipeline.py
      -> breaks non-official Full Manual fallback
      -> breaks configured Stage 1/2 compatibility
      -> strands telegram_bot/swing_report_builder assumptions

or:

    editing engine/quant/report behavior to make stale tests pass
      -> violates the frozen engine and approved presentation contracts

## Final Safety Assertion

ENGINE FILES MODIFIED: NONE

This audit created only this report. It did not delete, move, refactor, auto-fix,
stage, or commit any source, engine, config, test, documentation, runtime data,
database, historical data, generated artifact, cache, or local secret.
