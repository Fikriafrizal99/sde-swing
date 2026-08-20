# SDE Swing Scheduler Reliability

## Why this change exists

The previous Windows scheduler layer could miss or delay jobs even when the
underlying SDE engine was healthy:

1. Scheduled Market Outlook called `run_sde_job.py` directly while the manual
   workflow used `run_sde_job_integrated.py`.
2. Scheduled Post Market called `run_sde_job.py` directly while the manual
   workflow used the market-first integrated runner.
3. Windows tasks had no `RestartOnFailure`.
4. The XML generator hard-coded schedule times instead of reading
   `config/scheduler.json`.
5. Transient SDE exit states (`WAITING_DATA`, `RESOURCE_LOCKED`,
   `DELIVERY_FAILED`) had no scheduler-level retry policy.
6. The IDX disclosure BAT used `python` directly and was interactive on failure,
   which is unsafe for Task Scheduler.
7. There was no single command to install/update or inspect the managed Windows
   tasks.

## Managed tasks

The reliability layer manages:

- SDE Swing Market Outlook
- SDE Swing Post Market
- SDE Swing Final Watchlist
- SDE Swing IDX Disclosure Watcher

Daily times remain sourced from `config/scheduler.json`.

The IDX watcher uses an At-Logon trigger because its current successful IDX
transport is headed Playwright/Chrome and needs an interactive Windows session.

## Canonical execution paths

`tools/run_scheduled_job.py` deliberately does not implement trading logic. It
only calls the canonical workflow:

- Market Outlook -> `run_sde_job_integrated.py --job market_outlook`
- Post Market -> `run_sde_job_integrated_market_first.py --job post_market`
- Final Watchlist -> `tools/run_final_watchlist_entrypoint.py --period 1D`

This keeps manual and scheduled behavior aligned.

## Exit-code policy

SDE runtime exit codes remain authoritative:

- `0` success -> scheduler success
- `10` skipped -> scheduler success/no retry, except a confirmed active job lock
- `20` waiting data -> retry
- `30` duplicate -> scheduler success/no retry
- `40` resource locked -> retry
- `50` delivery failed -> retry
- `1` generic failure -> limited retry

A confirmed `EXIT_SKIPPED=10` caused by the job lock is treated as transient so
a manual/overlapping run does not make the scheduled run disappear silently.

Retry counts and backoff are configured under `scheduler_runtime` in
`config/scheduler.json`.

## Late starts

Windows cannot execute code while the laptop is shut down.

The tasks enable:

- WakeToRun
- StartWhenAvailable
- restart on process failure
- IgnoreNew for duplicate Task Scheduler instances

`tools/run_scheduled_job.py` records `start_lateness_minutes` and a warning when
a daily job starts later than the configured threshold. A late start is
observable but is not automatically discarded.

## Windows task installation

Run:

```bat
maintenance\INSTALL_SCHEDULERS.bat
```

The installer registers tasks for the current Windows account with
`InteractiveToken`. This avoids storing a Windows password and is compatible
with the headed browser flows currently used by IDX and optional broker
Playwright automation.

The tasks can run while Windows is locked, but not after sign-out or shutdown.

The installer disables legacy duplicate tasks only when their action points to
one of the same scheduler launchers in the current project directory.

It does **not** start the IDX watcher immediately, to avoid creating a duplicate
if a manually started watcher is already running. The managed IDX task starts
on the next logon.

## Health check

Run:

```bat
maintenance\CHECK_SCHEDULERS.bat
```

It reports:

- task presence/state
- last run
- next run
- Windows LastTaskResult
- restart configuration
- WakeToRun
- StartWhenAvailable
- launcher path consistency
- latest scheduler-wrapper state when available

`0x00000000` is the normal Windows LastTaskResult for a successful task process.

## Logs/state

Scheduler wrapper state:

`data/state/scheduler/scheduled_runs/<job>_latest.json`

Scheduler JSONL log:

`logs/sde_scheduler.log`

Normal SDE engine status and Telegram idempotency remain in their existing
locations. Scheduler retries do not change decision-engine calculations.

## Important laptop behavior

- Shutdown: no jobs can run.
- Sleep: WakeToRun can wake the laptop only if Windows/power settings allow
  wake timers.
- Locked desktop: InteractiveToken tasks can continue/run.
- Signed out: interactive browser tasks cannot run.
- When Windows becomes available after a missed daily trigger,
  StartWhenAvailable may launch the task late; lateness is recorded.
