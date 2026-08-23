# Watchlist AI Interpretation Architecture

Status: **ACTIVE ON `testing`**  
Scope: **Final Watchlist AI interpretation only**

## Purpose

Watchlist AI is a dedicated downstream interpretation lane for the completed
Final Watchlist. It explains how an AI reads the already-final SDE facts as a
swing-trading setup.

It is an **interpreter, not a decision engine**.

It may connect technical structure, the generated chart, execution plan, broker
summary/multi-day evidence, market context and risk into a natural Indonesian
narrative. It may quote official SDE numbers in equivalent presentation forms,
but it may not change or replace any SDE-owned result.

## Runtime boundary

The official Final Watchlist finishes first and remains authoritative:

```text
SDE engine / broker / decision / exit-plan stages
        |
        v
validated Final Watchlist facts
        |
        v
final_watchlist_ui.py
        |
        v
ReportPayload -> delivery.py -> Telegram Topic 9
        |
        | official child returns SUCCESS
        v
---------------- WATCHLIST AI BOUNDARY ----------------
        |
        v
tools/run_watchlist_ai.py
        |
        v
WatchlistAIService
        |
        v
provider #1 -> provider #2 -> provider #3
        |
        v
Watchlist AI numeric/fact validator
        |
        v
isolated JSON artifact + manifest
        |
        v
watchlist_ai_ui.py
        |
        v
ReportPayload -> delivery.py -> dedicated AI topic
```

`tools/run_final_watchlist_entrypoint.py` invokes the AI child only after the
canonical Final Watchlist child exits successfully. The AI child is explicitly
non-blocking and its return value never replaces the official Final Watchlist
return code.

If Final Watchlist fails, Watchlist AI is not launched.

If Watchlist AI fails, Final Watchlist remains successful.

## Isolation from News and IDX Disclosure AI

Watchlist AI is not an extension of the News or IDX Disclosure AI path.

It must not modify or reuse their runtime state, queue, cache, prompt, retry
budget, delivery semantics, document/PDF reader, message-edit flow, or report
artifact.

The following existing paths remain outside this subsystem:

- News Monitor / market-news processing;
- `modules/idx_disclosure/`;
- IDX Disclosure PDF/document AI reader;
- IDX Disclosure queue/state/message-edit flow;
- portfolio AI interpretation.

Watchlist AI owns:

```text
modules/ai_interpretation/watchlist/
data/state/ai_cache/watchlist/
data/output/ai_interpretation/watchlist/
tools/run_watchlist_ai.py
modules/telegram/watchlist_ai_ui.py
Telegram category: AI
```

Production Watchlist AI provider credentials also use dedicated environment
names so they are not implicitly coupled to credentials used by another AI
consumer.

## Official facts consumed

The primary source is the completed official Final Watchlist CSV for the exact
trade date:

```text
data/output/final_watchlist/sde-final-watchlist-<trade_date>.csv
```

The context builder exposes only approved Final Watchlist facts such as:

- ticker and trade date;
- SDE decision and final score;
- current/reference price;
- entry zone, stop loss, TP1, TP2 and RR;
- trend, technical state/score/quality, momentum, RSI and volume facts;
- support/resistance and phase when present in the official artifact;
- broker state/score/net flow;
- buyer/seller days and concentration;
- bandar/buyer cost and distance to cost;
- top buyers/sellers;
- multi-day broker flow, persistence and alignment;
- broker period/coverage metadata;
- market regime and sector state;
- exchange status/veto/risk flags;
- source/data-quality metadata.

No independent web search or second market-data lookup is performed by
Watchlist AI.

## Chart contract

When an existing Final Watchlist chart is available at the configured chart
output root, a vision-capable provider may receive it as additional context.

The chart is visual context only:

```text
chart interpretation        -> visual/structural context
structured SDE facts        -> numeric authority
```

An AI may discuss what it sees in the chart, but an official numeric level must
come from the structured context. It may not manufacture a new Entry, SL, TP,
support, resistance or other SDE level from the picture.

Providers without vision support still receive the same structured facts.

## Provider failover

The active provider chain is configuration-driven and sequential. At most three
configured providers are used:

```text
provider #1 SUCCESS -> stop
provider #1 FAIL    -> provider #2
provider #2 SUCCESS -> stop
provider #2 FAIL    -> provider #3
provider #3 SUCCESS -> stop
provider #3 FAIL    -> ALL_PROVIDERS_FAILED
```

Current repository defaults are:

1. OpenAI — vision enabled.
2. Gemini — vision enabled.
3. Groq — structured-facts fallback, vision disabled by default.

Provider/model order can be changed in `config/scheduler.json: watchlist_ai`
without changing engine code.

A provider attempt is considered failed on conditions such as:

- missing Watchlist-AI-specific API key;
- rate limit/quota error;
- timeout/transport/server error;
- empty/malformed response;
- unsupported response fields;
- response rejected by the numeric/fact validator.

Provider attempt history is persisted in the Watchlist AI artifact/manifest.

## Dedicated credentials

The active configuration expects these local environment variables:

```text
WATCHLIST_AI_OPENAI_API_KEY=
WATCHLIST_AI_GEMINI_API_KEY=
WATCHLIST_AI_GROQ_API_KEY=
TELEGRAM_THREAD_AI_ID=
```

These values belong in the local environment/`.env`, never in Git-tracked
configuration.

Using dedicated variable names prevents Watchlist AI from automatically
consuming another subsystem's API-key configuration. An operator may still
choose to place the same provider credential value in more than one variable,
but that is an explicit deployment choice rather than runtime coupling.

## AI response contract

Provider output is deliberately small structurally:

```json
{
  "analysis": "natural explanation based on supplied SDE facts",
  "conclusion": "concise closing view"
}
```

The Telegram formatter does not turn this into a second checklist-style SDE
report. The intended presentation is:

```text
🤖 AI VIEW — ANTM
📅 21 Agustus 2026 | SDE: WATCH

<natural AI explanation based on chart + SDE facts>

Kesimpulan: <short AI view>
```

Ticker, Final Watchlist trade date and official SDE decision are displayed
explicitly. Full company names are intentionally omitted.

## Numeric policy

AI is allowed to write numbers when they come from the supplied SDE context.
This includes price, Entry/SL/TP, RR, scores, RSI, broker values, net flow,
concentration, support/resistance and other factual numeric fields.

The value may be presented naturally without changing its meaning. For example:

```text
42600000000       -> Rp42,6 miliar
0.7502             -> 75,02%
3120               -> 3.120
```

The Watchlist-AI-only validator permits those equivalent transformations while
rejecting unsupported new numbers. Unit scaling is accepted only when the
corresponding magnitude unit is present, preventing a broker nominal from
silently authorizing an unrelated price level.

AI may not:

- change an official number;
- invent a new price/level;
- create AI Entry/SL/TP;
- create AI Score or success probability;
- replace the SDE decision;
- write back into SDE artifacts.

## Artifact and cache

Per-symbol results are stored separately from all official SDE artifacts:

```text
data/output/ai_interpretation/watchlist/<trade_date>/
  <SYMBOL>.json
  manifest.json
```

Each symbol artifact records:

- symbol and trade date;
- official SDE decision;
- AI status;
- provider/model actually used;
- analysis and conclusion;
- ordered provider attempts and failure reason summaries;
- context hash;
- read-only source context;
- generation timestamp.

Cache is isolated at:

```text
data/state/ai_cache/watchlist/
```

A cache key includes provider, model and context hash.

## Telegram routing

Watchlist AI has category `AI` in `TelegramRouter`.

Its dedicated topic is resolved from:

```text
TELEGRAM_THREAD_AI_ID
```

`tools/run_watchlist_ai.py` refuses main-chat fallback. If the AI topic ID is
missing, no AI message is allowed to leak into Topic 9, News/IDX, Signal,
System, or generic Report topics. The result is recorded as:

```text
SKIPPED_AI_TOPIC_NOT_CONFIGURED
```

Official Final Watchlist continues to use Topic 9 unchanged.

## AI failure notification

`watchlist_ai.notify_on_failure=true` enables informational Telegram output when
all three providers fail for one or more symbols or the isolated AI subsystem
cannot consume its official source artifact.

Example semantics:

```text
⚠️ WATCHLIST AI — INFORMASI
📅 21 Agustus 2026

Interpretasi AI gagal untuk: BBCA, ANTM.
Semua provider gagal atau responsnya ditolak validator.

Final Watchlist resmi tetap berhasil dan tidak ada keputusan/level SDE yang diubah.
```

This notification uses the same dedicated AI topic. It is informational only.
It must not trigger a Final Watchlist rollback, recalculation or scheduler
failure.

## Legacy embedded Final Watchlist AI

The old presentation interpreter remains available for unrelated legacy report
behavior, but its Final Watchlist call budget is now zero:

```text
enhanced_reporting.ai_interpretation.max_watchlist_calls = 0
```

The compatibility `GeminiInterpreter()` facade returns the existing Groq
interpreter with a zero Final Watchlist budget. This prevents the official
Final Watchlist builder from making its old embedded Final Watchlist AI calls.

The dedicated downstream `WatchlistAIService` is now the only intended AI path
for Final Watchlist interpretation.

## Operational sequence

Normal canonical Final Watchlist execution is now:

```text
1. Run official Final Watchlist as before.
2. Build official report/chart/CSV as before.
3. Deliver official Final Watchlist as before.
4. Official child returns success.
5. Launch tools/run_watchlist_ai.py for the same trade date.
6. Read official Final Watchlist CSV and existing chart.
7. Interpret Top N (default 5) through sequential provider failover.
8. Validate the response against SDE facts.
9. Persist AI artifact/manifest.
10. Deliver natural AI narrative to the dedicated AI topic.
11. If all providers fail, send informational AI failure notice instead.
```

Turning `watchlist_ai.enabled` off leaves the official Final Watchlist flow
unchanged.

## Regression requirements

Changes to Watchlist AI must preserve all of the following:

- no engine/quant/canonical/lifecycle modifications;
- no change to Final Watchlist decision/score/price/plan outputs;
- no change to approved Topic 9 Final Watchlist presentation;
- no change to News Monitor behavior;
- no change under `modules/idx_disclosure/`;
- no Watchlist AI main-chat fallback;
- AI starts only after official Final Watchlist success;
- official Final Watchlist failure prevents AI launch;
- all-provider failure returns an AI status/artifact instead of raising into SDE;
- supported numeric presentation transforms are accepted;
- invented numeric levels are rejected.

Dedicated regression coverage lives in:

- `tests/test_watchlist_ai_isolation.py`
- `tests/test_watchlist_ai_service.py`
- `tests/test_watchlist_ai_runner.py`
- `tests/test_watchlist_ai_numeric_validator.py`

The full repository CI/release validators remain authoritative for proving that
frozen engine and runtime contracts were not changed.
