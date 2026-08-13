# SDE Stabilization — Commit 4 Runtime / Status / Locking

## Audit basis

Commit 4 owns two confirmed P1 findings from `docs/SDE_AUDIT_BASELINE.md`:

- `AF-P1-003` — resend/delivery can overwrite the engine `*_latest.json` status;
- `AF-P1-004` — interruption can leave an inconsistent terminal state, including
  missing terminal evidence or a failed state carrying exit code zero.

Commit 4 does **not** own the shared `FINAL_DECISION_V2.csv` publication lock.
That resource is owned by Commit 2. It also does not change canonical market
data routing, decision scoring, candidate logic, entry/SL/TP calculations, or
any production quant parameter.

## Root causes confirmed

### 1. Resend reused the engine status writer

`tools/resend_daily_report.py` and `tools/resend_final_watchlist.py` invoke
`write_status()` with the same job names used by the engine. The audited writer
always updates `<job_status>/<job>_latest.json`. Therefore a delivery-only
resend can replace the latest engine record even though `engine_status=NOT_RUN`.

### 2. Delivery state and engine state shared one terminal record

Normal and integrated jobs can finish engine work and then fail Telegram
delivery. The previous schema did not expose a durable independent delivery
status channel, making engine truth and delivery truth unnecessarily coupled.

### 3. `KeyboardInterrupt` bypassed runner exception handlers

Both `run_sde_job.py` and `run_sde_job_integrated.py` catch `Exception`.
`KeyboardInterrupt` derives from `BaseException`, so it can cross the runner
without a terminal `write_status()` call. The job lock then releases while the
latest status can remain `RUNNING / START`.

### 4. Failed status accepted exit code zero

The status writer accepted the caller integer without enforcing the minimum
terminal invariant `FAILED => nonzero exit_code`.

### 5. Lock release was path-based, not owner-based

The baseline `FileLock.__exit__()` unlinked the lock path if it existed. If the
original lock were removed/replaced externally before cleanup, the old owner
could unlink a successor lock.

### 6. Stale PID checks were not host-aware

A PID stored by another host was probed against the local process table. On
shared state storage, PID identity is meaningful only on the creating host.

## Implementation

### Runtime status contract

`modules/job_runner/runtime.py` is now a small Commit-4 facade over the audited
byte-for-byte implementation retained as:

`modules/job_runner/runtime_baseline.py`

Contract version: `SDE_RUNTIME_STATUS_V1`.

The public job-runner import path and call signatures remain unchanged.

### Engine channel

Existing canonical engine/job files remain:

- `<job_status>/<trade_date>/<job>_<run_id>.json`
- `<job_status>/<job>_latest.json`

Commit 4 adds explicit fields:

- `status_channel = ENGINE`
- `engine_status`
- `delivery_status`
- `process_status`
- `runtime_status_contract`

The legacy/process `status` field remains available for compatibility.

### Delivery / resend channel

Delivery and resend are independently written to:

- `<job_status>/delivery/<trade_date>/<job>_<run_id>_<operation>.json`
- `<job_status>/<job>_delivery_latest.json`

A delivery-only resend is detected from its resend stage/delivery mode and is
routed exclusively to this channel. It records source engine run/status/hash
and declares `engine_mutation = NONE`. It never writes `<job>_latest.json`.

Normal engine runs may still return `EXIT_DELIVERY_FAILED` when Telegram fails,
preserving process-level failure behavior, while engine and delivery outcomes
remain independently observable.

### Terminal exit invariant

A terminal `FAILED + exit_code=0` is normalized to `FAILED + exit_code=1` and
records `EXIT_CODE_NORMALIZED_FROM_ZERO_FOR_FAILED_STATUS`.

The same invariant is applied to the secondary `modules/runtime/status.py`
writer.

### Interrupt terminalization

The job lock is the final runtime boundary shared by normal and integrated
runners. When an exception crosses that boundary before terminal status exists,
the lock terminalizes the run **before** release.

For `KeyboardInterrupt`:

- status: `FAILED`
- stage: `INTERRUPTED`
- durable exit code: `130`
- `finished_at`: populated
- errors: populated
- traceback: `<ctx.status_root>/tracebacks/...`
- job lock: released after terminal evidence

The interrupt is not swallowed; normal Python/OS interrupt propagation remains
non-successful.

### Ownership-safe locks

Each new lock contains a random `lock_token` plus run ID, PID and host. An owner
unlinks a lock only when the current file still matches its token, run ID, PID
and host. If the path has been replaced, cleanup logs an ownership mismatch and
leaves the successor lock intact.

### Host-aware stale cleanup

A lock can be removed when either:

1. age exceeds `stale_after_minutes`; or
2. recorded host equals the current host and that local PID is no longer alive.

A fresh foreign-host lock is never declared stale solely because its PID does
not exist locally. The stale candidate is re-read immediately before unlink;
changed content aborts cleanup.

### Status-write atomicity

Commit 4 uses unique temporary files plus `os.replace()` for **runtime status
evidence only**. Public generic `write_json()` behavior used by unrelated
artifacts is deliberately not changed, avoiding scope creep into the broader
P2 JSON-writer hardening item.

## Regression coverage

New targeted file: `tests/test_runtime_status_commit4.py`.

It covers:

- resend cannot mutate engine latest;
- normal delivery creates a separate delivery channel;
- failed terminal cannot keep exit code zero;
- `KeyboardInterrupt` terminalizes before lock release;
- interrupt traceback is durable;
- fresh foreign-host lock survives local PID probing;
- dead same-host lock can be reclaimed;
- an old lock owner cannot unlink a replacement owner's lock.

Commit 6 must still execute the full suite and final audit; targeted regression
existence is not a claim that the whole repository is green.

## New findings recorded

- `NF-C4-001` — resend paths reused the engine latest writer.
- `NF-C4-002` — uncaught `KeyboardInterrupt` could release job lock without
  terminal state.
- `NF-C4-003` — failed status accepted exit code zero.
- `NF-C4-004` — lock release lacked ownership identity.
- `NF-C4-005` — stale PID logic was not host-aware.
- `NF-C4-006` — some pre-existing explicit exception paths build traceback
  paths with a repository-default path instead of `ctx.status_root`; they
  already produce terminal traceback evidence and are therefore path
  normalization cleanup, not a blocker for AF-P1-004. Track after P0/P1
  stabilization unless Commit 6 proves it release-blocking.

## Quant and scope guard

Deliberately unchanged:

- `MODERATE_BASELINE`;
- production scoring weights and thresholds;
- hard blockers;
- candidate selection;
- broker fusion math;
- entry zone / initial SL / TP1 / TP2 calculations;
- RR/risk configuration;
- `auto_entry_enabled=false`;
- canonical data-source boundary/date-conflict behavior (Commit 5);
- shared V2 writer serialization (Commit 2);
- Telegram idempotency race (P2).

## Acceptance

Commit 4 is `IMPLEMENTED / PENDING RE-AUDIT` when:

- resend does not modify engine `*_latest.json`;
- delivery/resend has a distinct durable status channel;
- `FAILED` cannot be published with exit code zero;
- interrupt creates terminal status/error/traceback before job-lock release;
- stale cleanup is host-aware;
- lock release is ownership-safe;
- targeted regressions exist;
- no quant/data-path scope is modified.

`AF-P1-003` and `AF-P1-004` become `CLOSED BY RE-AUDIT` only after Commit 6.
