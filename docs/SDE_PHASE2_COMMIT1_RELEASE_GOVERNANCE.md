# SDE Phase 2 — Commit 1 Release Governance

## Baseline

- Repository: `Fikriafrizal99/sde-swing`
- Branch: `audit/sde-stabilization`
- Direct parent: `662c5ef160c7978237abc58e73e897190daf0632`
- Scope: release workflow governance and security only

The historical audit baseline `121bc58b0f6a62dc3a844ee59575fe48ce86cc7d`
remains a historical quant-freeze reference. It is not the Phase 2 working
baseline.

## Audit Finding

`AF-P2-002`

Status: **IMPLEMENTED / PENDING PHASE 2 RE-AUDIT**

Final closure requires the Phase 2 re-audit. Implementation alone does not
close the finding.

## Pre-Change Risk

The hotfix workflow held `contents: write`, checked out a fixed remote branch,
rewrote production source and a test through inline Python, created a commit,
and pushed that commit directly to the branch. This bypassed the normal
pull-request review and approval path and left repository credentials available
to the job.

## Workflow Before

`.github/workflows/hotfix-exit-volume.yml` performed this sequence:

1. Checkout the fixed source branch with full history.
2. Rewrite `modules/exit_engine/exit_engine.py` locally.
3. Create `tests/test_exit_engine_volume_state.py` locally.
4. Run a narrow compile and test check.
5. Stage, commit, and push the generated change to the source branch.

## Workflow After

The workflow is now a read-only validation workflow:

1. Checkout the exact triggering revision without persisting credentials.
2. Install the same Python validation dependencies used by repository CI.
3. Inspect the existing volume-state contract without changing it.
4. Compile the Exit Engine facade and frozen baseline implementation.
5. Run the existing signal-quality regression and governance regression.
6. Assert that no tracked or staged diff was created by validation.

It runs for relevant pull requests, relevant pushes to `main`, `agent/**`, and
`audit/**`, and explicit manual validation. None of these triggers grant a
remote mutation capability.

## Permission Changes

| Property | Before | After |
|---|---|---|
| Repository permission | `contents: write` | `contents: read` |
| Checkout target | Fixed mutation branch | Triggering revision |
| Credential persistence | Checkout default | `persist-credentials: false` |
| New PAT or secret | None | None |

## Direct Mutation Paths Removed

The implementation removed every workflow path that previously:

- rewrote production source;
- generated a tracked test file;
- configured a bot commit identity;
- staged repository changes;
- created a commit;
- pushed directly to a remote branch.

No replacement mutation helper, automatic PR creator, branch-ref updater, or
token-based mutation path was introduced.

## Security Properties

- Least-privilege repository permission is explicit.
- Checkout credentials are not persisted in local Git configuration.
- The workflow does not receive a PAT or a new secret.
- The workflow does not use `pull_request_target`.
- The pull-request path validates untrusted changes without write permission.
- Any required code correction must follow the normal reviewed change process.
- Validation finishes by checking that the tracked and staged trees are clean.

## Validation Performed

| Validation | Local result |
|---|---|
| Workflow YAML parse with `PyYAML 6.0.3` `BaseLoader` | PASS |
| Effective permission and trigger assertions | PASS |
| Static mutation/token scan | PASS |
| Workflow governance + signal-quality target | **12 passed, 3 subtests passed** |
| `python -m compileall -q .` | PASS |
| `python tools/ci_validate_quant_freeze.py` | Known `PA2-NF-001` Windows CRLF false failure |
| `python tools/ci_validate_stabilization_release.py` | Stops on the same `PA2-NF-001` result |
| Full `python -m pytest -q` | **566 passed, 2 failed, 3 subtests passed** |
| Full-suite failures | Only known `PA2-NF-001` and `PA2-NF-002` |
| `git diff --check` | PASS |
| Protected quant-source diff | Empty |

The YAML parser was installed only in a temporary validation directory; no
dependency was added to the repository. A GitHub Actions run on the final commit
remains the authoritative hosted-run confirmation on the repository's Linux
runner.

## Known Limitations

- Local Windows validation may reproduce `PA2-NF-001`, the known quant-freeze
  CRLF false failure.
- Local Windows validation may reproduce `PA2-NF-002`, the known resend-test
  path-separator failure.
- The workflow validates the already-integrated volume-state behavior. It no
  longer prepares or publishes a code patch for an operator.

## Deferred Findings

This commit does not implement or close:

- `AF-P2-001` Telegram idempotency transaction locking;
- `AF-P2-003` database revision immutability;
- `NF-C2-001` generic CSV temporary-path concurrency;
- `NF-C2-002` generic JSON atomicity;
- `NF-C3-003` contextual exit reproducibility;
- `NF-C4-006` legacy traceback routing;
- `NF-C5-003` provider ownership wording;
- `NF-C5-004` manager-wide BEI calendar injection;
- `PA2-NF-001` quant-freeze Windows portability;
- `PA2-NF-002` resend-test Windows portability.

## Quant / Trading Invariance

No trading logic changed.

No quant logic changed.

No autonomous trading capability enabled.

`auto_entry_enabled` remains `false`.

The following stabilization contracts remain unchanged:

- `MODERATE_BASELINE`;
- `SHADOW_ONLY`;
- quant freeze;
- lifecycle contract;
- runtime status contract;
- canonical data contract;
- V2 artifact publication contract.
