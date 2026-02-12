# Gmail Lead Scanner v1

Script: `scripts/gmail_lead_scanner.py`

## What it does
- Pulls latest inbox messages via `gog gmail messages search` (message-level)
- Extracts sender, subject, date, snippet/body, message id + thread id
- Classifies each email with OpenAI (`gpt-4o-mini` default) in strict JSON
- Deterministically maps to tracker status
- Imports/updates prospects in `outreach.db` with dedupe guard
- Saves summary + sentiment + source ids in `notes`
- Defaults to **dry-run** unless `--apply` is passed
- Prints audit table per message

## Status mapping implemented
- `lost`: explicit not interested sentiment
- `client`: paying/signed/invoice indicators
- `pilot`: pilot/trial/POC accepted indicators
- `closed`: high intent to proceed
- `call_scheduled`: explicit call/meeting request
- `responded`: clear proposition/proposal/partnership signal
- `contacted`: outreach pitch with confidence
- `new`: low confidence possible outreach

## Dedupe guard
Creates table `gmail_import_log` with uniqueness on:
- `(sender_email, message_id)`
- `(sender_email, thread_id)`

This prevents duplicate imports for the same sender + message/thread.

## Requirements
- `gog` CLI authenticated for Gmail
- `OPENAI_API_KEY` set
- Python deps in `requirements.txt` (`requests` already included)

## Run
Dry-run (default):

```bash
python3 scripts/gmail_lead_scanner.py --max 30
```

Apply writes:

```bash
python3 scripts/gmail_lead_scanner.py --max 30 --apply
```

Optional flags:
- `--account you@gmail.com` (gog account)
- `--db ./outreach.db`
- `--model gpt-4o-mini`
- `--min-confidence 0.35`
