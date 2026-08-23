# IDX AI Document Reader V1

## Scope

The AI reader is an optional downstream module of the isolated IDX Disclosure Watcher.
It does not import, call, lock, or write to the SDE broker, technical, scoring, candidate,
or final-decision engines.

Runtime flow:

```text
IDX -> Playwright -> normalize/dedup -> official Telegram NEWS -> mark official delivered
                                                        |
                                                        v
                                             durable AI queue (SQLite)
                                                        |
                                              local PDF text extraction
                                                        |
                                                    Groq summary
                                                        |
                                             edit official Telegram NEWS message
```

The official IDX message is always the first delivery. AI generation is eligible only
when the official disclosure row already has `telegram_sent_at`. A Groq/PDF failure can
therefore never prevent the official disclosure from being delivered.

## Cost controls

- No Groq call is made by the polling loop when there is no pending AI work.
- Baseline and dry-run disclosures are not queued for AI.
- New live disclosures are queued once by `Id2`.
- AI-ready JSON is persisted before Telegram delivery, so a Telegram retry never calls
  Groq a second time.
- At most one new document is summarized per poll by default.
- Main document is read first. Up to two attachments are read only when the main document
  has little extractable text or explicitly references attachments.
- PDF extraction is local with `pypdf`; Groq receives cleaned/capped text, not the raw PDF.
- Input is capped at 50,000 characters by default and output at 850 tokens.
- Retry backoff defaults to 60 / 300 / 900 seconds, maximum three generation attempts.

## Safety contract

The prompt forbids sentiment, score, price prediction, BUY/SELL/HOLD, and trading decisions.
The AI Telegram message is explicitly labeled as an automated summary and directs the user
to verify the official IDX document.

`config/idx_disclosure.json` keeps `decision_engine_write_access=false` at both the watcher
and AI-reader safety layers.

## Configuration

AI is enabled in `config/idx_disclosure.json`, but it becomes active only when all conditions
are met:

1. `pypdf` is installed (`pip install -r requirements.txt`).
2. `GROQ_IDX_API_KEY` is available in the process environment or local `.env` file. This is
  the key named by `ai_reader.api_key_env`; do not assume `GROQ_API_KEY` is read.
3. The watcher is running in live delivery mode with `--telegram` (or
  `delivery.enabled=true`). Dry-run mode and `--no-ai` deliberately disable AI.

Optional model override:

```text
GROQ_IDX_MODEL=llama-3.3-70b-versatile
```

Do not commit `.env` or any API key.

## Commands

Normal watcher; AI activates automatically when configured and available:

```powershell
python run_idx_disclosure_watcher.py --watch --telegram --transport playwright
```

Emergency AI bypass while preserving official IDX delivery:

```powershell
python run_idx_disclosure_watcher.py --watch --telegram --transport playwright --no-ai
```

Explicitly summarize the latest already-delivered disclosure once (useful for validation):

```powershell
python run_idx_disclosure_watcher.py --telegram --transport playwright --ai-backfill-latest 1
```

Backfill is opt-in. Normal startup does not summarize historical disclosures.

## Why AI Can Appear Intermittent

AI is downstream of the official IDX notification. A new disclosure is queued only after it
is stored, and generation is eligible only after the official Telegram delivery succeeds.
The first poll on an empty database seeds a baseline without delivery or AI, so existing
announcements are not summarized automatically.

The default limit is one document per poll. Pending work is retried after 60, 300, and 900
seconds, with a maximum of three generation attempts. Overnight polling can therefore make
the queue appear idle for up to 600 seconds even when the process is healthy.

Use the queue state command to distinguish the cases:

```powershell
python tools/check_idx_ai_state.py --limit 20
```

| Queue state or error | Meaning |
| --- | --- |
| No queue row | AI was disabled, the row was baseline-seeded, or official delivery has not succeeded. |
| `PENDING` / `RETRY` | Work is waiting for its next poll or retry time. |
| `SENT` | Summary was generated and delivered successfully. |
| `PERMANENT_FAILED` with `GROQ_HTTP_413` | Extracted document text exceeded the model/service token limit; reduce `max_input_chars` or use a model/tier with a higher limit. |
| `PERMANENT_FAILED` with `PDF_PARSE_FAILED` | The IDX file is malformed, scanned, or not a parseable PDF. |
| `PERMANENT_FAILED` with `IDX document HTTP 404` | The IDX attachment link was unavailable or rejected by the browser session. |

AI failures do not block the official IDX message. A permanent failure must be re-queued
explicitly after correcting the document, model, or configuration; it will not retry forever.

## Runtime telemetry

The poll JSON now includes:

- `ai_reader`
- `ai_queued`
- `ai_generated`
- `ai_delivered`
- `ai_failed`

A healthy new disclosure with AI typically progresses from official `delivered=1` to
`ai_generated=1` and `ai_delivered=1`. If AI fails, `delivery_failed` for the official
message can still remain zero; the AI error stays in the isolated AI queue for bounded retry.
