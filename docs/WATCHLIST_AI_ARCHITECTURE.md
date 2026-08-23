# Watchlist AI Interpretation Architecture

Status: **TARGET ARCHITECTURE — implementation not yet applied**  
Branch: `testing`  
Scope: **Final Watchlist AI interpretation only**

## 1. Purpose

SDE Swing will expose a dedicated AI interpretation path for Final Watchlist symbols.
The AI explains how it reads the already-final SDE facts as a swing-trading setup.

The AI is an **interpreter, not a decision engine**.

It may connect technical structure, chart context, broker evidence, multi-day flow,
execution plan, market regime, and risk into a natural-language opinion. It must
not change any SDE-owned result.

The official Final Watchlist path and its approved Telegram presentation remain
independent from this subsystem.

## 2. Isolation from other AI systems

This subsystem is intentionally isolated from every existing non-watchlist AI path.
In particular it must **not modify, route through, replace, or share runtime state**
with:

- News Monitor AI / market-news interpretation;
- IDX Disclosure watcher AI document reader;
- IDX Disclosure queue, PDF reader, summary formatter, message-edit flow, or state;
- portfolio AI interpretation;
- any future AI consumer outside Final Watchlist.

The existing IDX Disclosure AI reader remains downstream of official IDX delivery,
keeps its own configuration and failure policy, and is not part of this architecture.

Watchlist AI must have its own namespace for config, cache/artifacts, status, provider
routing, Telegram report type, and tests. Provider credentials may reference the same
environment secret names where operationally desired, but the service instances,
budgets, retry state, cache keys, prompts, and delivery behavior remain independent.

## 3. Core architecture

```text
SDE ENGINE / FINAL WATCHLIST
        |
        v
Validated Final Watchlist Facts
        |
        +-------------------------------+
        |                               |
        v                               v
OFFICIAL SDE PATH                 WATCHLIST AI PATH
Final Watchlist builder           WatchlistAIService
        |                               |
final_watchlist_ui.py             WatchlistAIContextBuilder
        |                               |
ReportPayload                     WatchlistAIProviderRouter
        |                               |
delivery.py                       AI #1 -> AI #2 -> AI #3
        |                               |
TelegramRouter                    WatchlistAIResponseValidator
        |                               |
        v                               v
Topic 9                           Watchlist AI artifact
SDE OFFICIAL                            |
                                      watchlist_ai_ui.py
                                            |
                                      ReportPayload
                                            |
                                      delivery.py
                                            |
                                      TelegramRouter
                                            |
                                            v
                                      dedicated AI topic
```

Turning off the complete Watchlist AI subsystem must leave the official Final
Watchlist result, artifacts, Telegram output, lifecycle, database/archive, and engine
behavior unchanged.

## 4. Runtime ordering

The required order is:

```text
1. SDE engine stages finish
2. Final Decision and Entry/Exit Plan finish
3. Final Watchlist facts are validated
4. Official Final Watchlist artifacts are built
5. Official Final Watchlist is delivered to Topic 9
6. Official Final Watchlist is considered successful

---------------- OFFICIAL SDE BOUNDARY ----------------

7. Watchlist AI starts
8. Build one AI context package per selected symbol
9. Optionally attach the already-generated Final Watchlist chart
10. Try AI provider #1
11. On provider failure, try provider #2
12. On provider failure, try provider #3
13. Validate the AI response against SDE facts
14. Persist the separate Watchlist AI artifact/status
15. Format the Watchlist AI Telegram message
16. Deliver only to the dedicated Watchlist AI topic
```

Steps 7-16 are **non-blocking** relative to steps 1-6.

If every AI provider fails:

```text
Final Watchlist: SUCCESS
Watchlist AI: ALL_PROVIDERS_FAILED / SKIPPED
```

No failure in Watchlist AI may downgrade, roll back, or retry the official Final
Watchlist delivery.

## 5. Read-only context package

The AI context is assembled only from validated/current SDE artifacts for the same
trade date and symbol.

Expected context groups:

```text
identity
  symbol
  trade_date

final_result
  decision
  final_score

technical
  trend
  technical_state
  technical_score / quality
  momentum
  RSI
  volume facts
  support
  resistance
  chart reference when available

plan
  current/reference price
  entry_low
  entry_high
  stop_loss
  target_1
  target_2
  risk_reward

broker
  broker_state
  broker_score
  net_flow
  buy_days / sell_days
  buyer/seller concentration
  bandar_buy_cost
  distance_to_buy_cost
  top_buyers
  top_sellers
  multi_day_flow
  persistence / alignment
  period/coverage metadata

market_context
  market_regime
  sector_state
  exchange_status
  exchange_veto
  risk_flags
  data_quality / source status
```

No web search or independent market-data lookup is required by Watchlist AI. The
model explains the SDE package supplied to it.

## 6. Chart rule

The Final Watchlist chart may be provided to a vision-capable AI provider as
additional visual context.

The chart is **not an independent source of official numeric levels**.

- visual chart reading may help the model discuss structure, momentum, compression,
  rejection, trend shape, or price behavior;
- official numbers such as Entry, Stop Loss, TP, support, resistance, scores, and
  broker values come from the SDE context package;
- the AI may quote those official numbers in its narrative;
- the AI may not create a replacement official level from visual estimation.

Providers without vision support receive the same structured facts without the image.

## 7. AI permissions and immutable facts

AI may:

- explain and connect the supplied facts;
- express a perspective such as attractive, cautious, not comfortable chasing,
  waiting for confirmation, or liking/disliking the risk placement;
- quote official SDE numbers exactly as supplied;
- discuss why technical and broker evidence agree or conflict;
- explain what confirmation it would prefer to see next.

AI must never:

- modify Decision, Final Score, Entry, Stop Loss, TP1, TP2, Risk/Reward, technical
  score, broker score, market regime, or other engine-owned fields;
- create an AI Decision, AI Score, probability of success, or replacement signal;
- invent a new official Entry/SL/TP/support/resistance value;
- write into engine artifacts, lifecycle state, portfolio state, canonical data, or
  the official Final Watchlist artifact;
- affect News or IDX Disclosure AI output/state.

The existing immutable-field and unsupported-number validation concept should be
retained and adapted to this dedicated subsystem.

## 8. Provider failover

Watchlist AI supports exactly the configured ordered provider chain. Provider names
and models are configuration, not hard-coded runtime assumptions.

Example target configuration shape:

```json
{
  "watchlist_ai": {
    "enabled": true,
    "non_blocking": true,
    "max_symbols": 5,
    "providers": [
      {"provider": "OPENAI", "model": "<model>", "priority": 1},
      {"provider": "GEMINI", "model": "<model>", "priority": 2},
      {"provider": "GROQ", "model": "<model>", "priority": 3}
    ]
  }
}
```

Execution:

```text
provider #1 SUCCESS -> stop
provider #1 FAIL    -> provider #2
provider #2 SUCCESS -> stop
provider #2 FAIL    -> provider #3
provider #3 SUCCESS -> stop
provider #3 FAIL    -> ALL_PROVIDERS_FAILED
```

Failures include timeout, quota/rate limit, transport error, invalid/malformed
response, validation rejection, missing key, or unavailable model.

Failover state belongs only to Watchlist AI. It must not consume or modify retry
budgets/state used by News or IDX Disclosure AI.

## 9. AI response contract

Telegram output should be natural explanation, not a checklist-style second SDE
report.

The provider response can remain structurally small:

```json
{
  "analysis": "2-4 natural paragraphs explaining the AI perspective",
  "conclusion": "one concise concluding sentence"
}
```

The analysis may mention any relevant supplied SDE numbers, but every numeric claim
must be traceable to the Watchlist AI context package.

## 10. Telegram presentation

Dedicated report type:

```text
watchlist_ai_interpretation
```

Dedicated topic label/category:

```text
ai_watchlist / AI
```

Target message shape:

```text
🤖 AI VIEW — ANTM
📅 21 Agustus 2026 | SDE: WATCH

<2-4 natural explanatory paragraphs based on chart + technical + plan + broker +
market context. Relevant official numbers may be quoted.>

Kesimpulan: <short AI perspective>
```

The Telegram report intentionally does not repeat issuer full name, provider audit
metadata, source paths, generated timestamp, or the complete official Final Watchlist
card. Those belong in artifacts/logs, not the reader-facing message.

The displayed date is the Final Watchlist **trade_date**, not the wall-clock time at
which AI generation happened. This keeps historical recovery/resend unambiguous.

## 11. Telegram routing

Official routing remains unchanged:

```text
final_watchlist_summary -> Topic 9
final_watchlist_detail  -> Topic 9
final_watchlist_csv     -> Topic 9
```

Watchlist AI receives its own route:

```text
watchlist_ai_interpretation -> dedicated Watchlist AI topic
```

The current centralized `ReportPayload -> delivery.py -> TelegramRouter` delivery
boundary should be reused. A second Telegram sender must not be introduced.

News and IDX Disclosure routes remain untouched.

## 12. Artifact and audit boundary

Watchlist AI artifacts are separate from official SDE artifacts and from every other
AI subsystem.

Target namespace:

```text
data/output/ai_interpretation/watchlist/<trade_date>/
  <SYMBOL>.json
  manifest.json
```

Target state/cache namespace:

```text
data/state/ai_cache/watchlist/
data/state/watchlist_ai/
```

They must not share cache directories, queue state, or manifests with IDX Disclosure
AI or News AI.

Per-symbol artifact should include at least:

```text
symbol
trade_date
sde_decision
source/context signature
provider_used
model_used
analysis
conclusion
generated_at
validation_status
```

The manifest may retain provider attempts and sanitized failure reasons for audit.
Provider attempt details are not required in the Telegram message.

## 13. Proposed module ownership

Target modules:

```text
modules/ai_interpretation/watchlist/
  __init__.py
  service.py
  context.py
  provider_router.py
  validator.py
  providers/
    base.py
    openai_provider.py
    gemini_provider.py
    groq_provider.py

modules/telegram/watchlist_ai_ui.py
modules/job_runner/watchlist_ai.py
```

Existing general/legacy `gemini_interpreter.py` and `groq_interpreter.py` must not be
deleted during the first implementation. They may still serve existing report or
compatibility contracts. Migration/retirement is a separate cleanup decision.

Existing IDX Disclosure modules under `modules/idx_disclosure/` are explicitly out
of scope and must not be changed for Watchlist AI implementation.

## 14. Configuration ownership

New Watchlist AI configuration must use a dedicated top-level/subtree namespace such
as `watchlist_ai`.

Do not repurpose:

- `idx_disclosure.ai_reader`;
- News Monitor configuration;
- disclosure queue/state settings;
- unrelated portfolio AI settings;
- generic legacy AI settings in a way that changes existing callers.

Existing configuration may remain in place until the Watchlist AI path is migrated
and verified.

## 15. Regression and acceptance requirements

Implementation is acceptable only if all of the following are proven:

1. Engine/quant/canonical/lifecycle protected files are unchanged.
2. Official Final Watchlist facts are identical before and after the change.
3. Official Final Watchlist Topic 9 text/artifacts remain unchanged.
4. Disabling Watchlist AI produces exactly the normal official runtime behavior.
5. One failed AI provider falls through only to the next Watchlist AI provider.
6. Three failed AI providers do not fail Final Watchlist.
7. Unsupported/new numeric values in AI output are rejected.
8. Official SDE numeric values may be quoted unchanged.
9. Watchlist AI routes only to its dedicated Telegram topic.
10. News Monitor behavior/routing is unchanged.
11. IDX Disclosure watcher, AI reader, queue, PDF processing, message edit flow,
    routing, and state are unchanged.
12. No Watchlist AI artifact/cache/status file is written into a News/IDX namespace.
13. Historical trade_date remains the displayed analysis date even when generated on
    a later wall-clock date.

## 16. Implementation phases

### Phase A — architecture and contracts

- this document;
- architecture boundary reference;
- no runtime behavior change.

### Phase B — isolated Watchlist AI core

- dedicated context model;
- provider interface/router;
- validator;
- artifact/status writer;
- unit tests;
- no Telegram integration yet.

### Phase C — provider adapters

- OpenAI adapter;
- Gemini adapter;
- Groq adapter;
- ordered failover tests;
- independent Watchlist AI budgets/cache.

### Phase D — presentation and routing

- `watchlist_ai_ui.py`;
- dedicated `watchlist_ai_interpretation` ReportPayload;
- dedicated Telegram route/topic;
- official Topic 9 regression proof.

### Phase E — runtime integration

- invoke Watchlist AI only after successful official Final Watchlist construction/
  delivery boundary;
- non-blocking status handling;
- end-to-end regression including News and IDX Disclosure isolation.

## 17. Non-goals

This project does not redesign or modify:

- Decision Engine;
- Technical Feature Engine;
- Candidate Selector;
- Broker Fusion or Broker Multi-Day calculations;
- Entry/Exit Engine;
- lifecycle/portfolio logic;
- canonical/source ownership;
- database archive semantics;
- Market Outlook AI in the first implementation;
- News Monitor AI;
- IDX Disclosure watcher or its AI document reader.

Any future reuse of the Watchlist provider router by another AI subsystem requires a
separate architecture decision and regression scope. It is not implicit in this
implementation.
