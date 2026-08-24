# FTJ Report Branding Contract

## Purpose

Human-facing SDE Swing reports belong to **FTJ Community**. `FTJ` means **Fikri Trade Journal**.

This contract owns report naming only. It does not own or change Technical, Broker, Decision, Entry/Exit, Lifecycle, News selection, scoring, thresholds, state transitions, Telegram routing, or data-source behavior.

## Canonical identity

- Community: `FTJ Community`
- Brand: `FTJ`
- Internal engine/project terminology may remain `SDE Swing` / `SDE` in code, logs, filenames, schemas, database fields, and technical documentation where it describes the engine rather than the human-facing report title.

## Canonical human-facing titles

| Report purpose | Canonical title |
| --- | --- |
| Market Outlook | `FTJ — MARKET PULSE` |
| Post Market | `FTJ — CLOSING PULSE` |
| Broker Summary | `FTJ — BROKER FLOW` |
| Final Watchlist | `FTJ — SWING WATCHLIST` |
| Active Recommendations | `FTJ — ACTIVE SETUPS` |
| Lifecycle Digest / material lifecycle status | `FTJ — POSITION UPDATE` |
| Morning News | `FTJ — MORNING BRIEF` |
| Post-Market News | `FTJ — MARKET NEWS` |
| Market Heatmap | `FTJ — MARKET HEATMAP` |

Stock detail cards do not need to repeat the FTJ brand when they are already delivered as children of an FTJ report. Their symbol, decision, score, entry, stop, targets, broker facts, and action text remain unchanged.

## Hard exclusion: IDX Disclosure

**IDX Disclosure / Keterbukaan Informasi is explicitly excluded from FTJ rebranding.**

The IDX Disclosure Watcher keeps its existing title and identity. Do not edit `modules/idx_disclosure/`, the watcher title, disclosure formatter, AI reader identity, or disclosure delivery wording merely to apply FTJ report branding.

The shared branding helper must return disclosure text unchanged whenever it identifies IDX Disclosure / Keterbukaan Informasi content.

## Runtime ownership

`modules/branding.py` is the canonical naming policy.

Integrated SDE jobs apply branding at the final `ReportPayload` boundary. This keeps engine and builder contracts stable while ensuring auto/manual/resend deliveries share the same visible report identity.

Active Recommendations and Lifecycle Digest have direct sender paths outside ordinary integrated payload construction. Those senders must apply the same `modules.branding.apply_ftj_branding()` helper after the canonical lifecycle formatter has produced its content.

News remains isolated from Watchlist AI, IDX Disclosure, and the Decision Engine. FTJ naming is applied only at the final Telegram split/delivery boundary; news filtering, ranking, freshness, dedupe signature, state, provider behavior, and routing must remain unchanged.

## Invariants

1. Branding is presentation-only.
2. Do not rename engine artifacts, CSV columns, database tables, config keys, report types, routing keys, or job names solely for branding.
3. Do not duplicate FTJ title mappings inside individual formatters. Add or change names in `modules/branding.py`.
4. `apply_ftj_branding()` must be deterministic and idempotent.
5. A report already branded as `FTJ — ...` must remain unchanged on a second pass.
6. IDX Disclosure / Keterbukaan Informasi must remain byte-for-byte unchanged by the branding helper.
7. Existing SDE calculations and report facts must be identical before and after branding, aside from the human-facing title text.
8. New human-facing SDE Swing reports should use the `FTJ — ...` namespace unless explicitly excluded by this contract.

## Regression requirements

Any future branding change must keep tests for:

- every canonical title mapping;
- idempotency;
- generic compatibility mapping for legacy non-IDX `SDE SWING — ...` titles;
- exact IDX Disclosure exclusion;
- runtime `ReportPayload` application;
- direct Active Recommendations / Lifecycle sender application;
- News delivery-only application without changing the pre-branding dedupe signature input.
