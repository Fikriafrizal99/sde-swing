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
                                             separate Telegram NEWS message
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

AI is enabled in `config/idx_disclosure.json`, but it becomes active only when both conditions
are met:

1. `pypdf` is installed (`pip install -r requirements.txt`).
2. `GROQ_API_KEY` is available in the process environment or local `.env` file.

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
