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

---

## Sent target audit (CRM intelligence v2)

Script: `scripts/gmail_sent_target_audit.py`

### What changed (phase 1 + phase 2)
- Two-stage precision-first filtering:
  - **Stage 1 hard filters**: self emails, role inboxes, notification/system senders/domains, learning-config skip domains/keywords
  - **Stage 2 rules gate**: estimates real two-way interaction from sent-thread evidence before calling a contact a target
- Labels unchanged from phase 1:
  - `prospect_target`
  - `existing_client`
  - `admin_non_target`
  - `uncertain`
- Calendar signal integration (phase 2):
  - Pulls Google Calendar `primary` events from last 60 days via `gog calendar events`
  - Counts only meetings with attendee count in `1..10` and duration `>= 15` minutes
  - Extracts attendee names/emails and maps only to contacts already in sent-email audit candidates
  - Adds cross-signal scoring boost when a contact appears in both sent email + calendar
- Audit output now includes:
  - `meeting_count`
  - `last_meeting_date`
  - `cross_signal`
  - (plus phase 1 fields: `exchange_estimate`, `reason_codes`, `confidence_bucket`)
- Learning config is externalized and editable in `scripts/learning.json`
- Import remains approval-first (`--import-email` + optional `--apply`), and only `prospect_target` can be imported.
- Apply path still merges duplicate same-email prospect rows before updating, so no duplicate contact rows persist.

### Learning config
Path: `scripts/learning.json`

Supported keys:
- `skip_domains`
- `prefer_titles`
- `skip_keywords`
- `min_exchanges` (default `1`)
- `max_days_between` (default `60`)
- `max_attendees` (default `10`)
- `min_duration_minutes` (default `15`)

### Run audit
Dry-run audit table:

```bash
python3 scripts/gmail_sent_target_audit.py --account you@gmail.com --max 40
```

If Calendar access is unavailable, the script prints `CALENDAR WARNING` and falls back to email-only scoring (no auto-import changes).

Dry-run import decision for one email:

```bash
python3 scripts/gmail_sent_target_audit.py --account you@gmail.com --import-email person@company.com
```

Apply import (approval-first):

```bash
python3 scripts/gmail_sent_target_audit.py --account you@gmail.com --import-email person@company.com --apply
```
