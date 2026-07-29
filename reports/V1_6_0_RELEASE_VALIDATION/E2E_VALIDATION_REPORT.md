# End-to-End Validation Report — SDE Swing V1.6.0 Signal Quality & Entry Readiness

- Run ID: `RELEASE-E2E-20260727-135514`
- Status: **PASS**
- Pipeline version: `SDE_SWING_V1_6_0_SIGNAL_QUALITY_ENTRY_READINESS`
- Technical success: 439 / 441
- Candidates: 30
- Broker coverage: 100%
- Decision rows independently checked: 30
- Master pipeline: SKIPPED_BOUNDED_RELEASE_CHECK (NOT_APPLICABLE)
- Master decision rows independently checked: 0
- Entry plans: 17 (approved 0)
- Telegram previews: 11

## Stage Results

| Stage | Status | Duration (s) |
|---|---:|---:|
| Python compile | PASS | 0.575 |
| Regression tests | PASS | 6.703 |
| Technical feature engine | PASS | 16.162 |
| Candidate selector | PASS | 0.670 |
| Broker fusion | PASS | 0.586 |
| Decision engine | PASS | 0.556 |
| Exit engine | PASS | 1.044 |
| Swing analytics | PASS | 1.194 |
| Database archive | PASS | 1.519 |
| Telegram dry run | PASS | 0.846 |

## Decision Counts

- AVOID: 11
- WATCH HIGH: 7
- BUY CANDIDATE: 6
- BUY: 3
- SPECULATIVE: 2
- STRONG BUY: 1

## Notes

- Yahoo network refresh was not called; packaged historical files were used.
- Broker data was generated as a complete deterministic fixture matching the current 30 candidates.
- Decision V3 quality, readiness, broker confidence, liquidity, score, and labels were independently recalculated for every output row.
- The canonical master_pipeline.py is covered by orchestrator regression tests; bounded release validation executes each production module independently.
- Two short-history symbols may be skipped by the technical engine without failing the pipeline.
