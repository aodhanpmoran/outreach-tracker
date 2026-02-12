#!/usr/bin/env python3
"""
Gmail lead scanner v1

- Fetches recent Gmail inbox messages via `gog gmail messages search`
- Classifies likely outreach with OpenAI (gpt-4o-mini by default)
- Maps to deterministic tracker statuses
- Creates/updates prospects with dedupe guard
- Supports dry-run by default; use --apply to write changes
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_PROVIDER = os.environ.get("LEAD_SCANNER_PROVIDER", "openai")
DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

STATUS_ORDER = {
    "new": 0,
    "contacted": 1,
    "responded": 2,
    "call_scheduled": 3,
    "closed": 4,
    "pilot": 5,
    "client": 6,
    "lost": 7,
}


@dataclass
class ParsedMessage:
    message_id: str
    thread_id: str
    sender_name: str
    sender_email: str
    subject: str
    date_raw: str
    date_iso: str
    snippet: str
    body: str


@dataclass
class Classification:
    is_business_outreach: bool
    outreach_type: str
    intent_level: str
    sentiment: str
    confidence: float
    summary: str


def run_gog_fetch(limit: int, account: Optional[str] = None, query: str = "in:inbox") -> List[Dict[str, Any]]:
    cmd = [
        "gog",
        "gmail",
        "messages",
        "search",
        query,
        "--max",
        str(limit),
        "--json",
        "--include-body",
        "--no-input",
    ]
    if account:
        cmd.extend(["--account", account])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "gog command failed")

    payload = json.loads(proc.stdout)
    if isinstance(payload, dict) and "messages" in payload:
        return payload.get("messages") or []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], list):
        return payload["data"]
    return []


def parse_sender(from_header: str) -> Tuple[str, str]:
    # Handles both RFC822 and simplified forms from gog output.
    name, addr = parseaddr(from_header or "")
    addr = (addr or "").strip().lower()
    if addr:
        clean_name = (name or "").strip().strip('"')
        return (clean_name or addr.split("@")[0], addr)

    m = re.match(r"\s*\"?([^\"<]*)\"?\s*<([^>]+)>\s*$", from_header or "")
    if m:
        parsed_addr = m.group(2).strip().lower()
        return (m.group(1).strip() or parsed_addr, parsed_addr)

    fallback = (from_header or "").strip().lower()
    return (fallback.split("@")[0] if "@" in fallback else fallback, fallback)


def header_value(headers: List[Dict[str, str]], key: str) -> str:
    for h in headers or []:
        if (h.get("name") or "").lower() == key.lower():
            return h.get("value") or ""
    return ""


def decode_b64url(data: str) -> str:
    if not data:
        return ""
    pad = "=" * ((4 - len(data) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode((data + pad).encode("utf-8"))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_body(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""

    body_data = ((payload.get("body") or {}).get("data"))
    text = decode_b64url(body_data) if body_data else ""

    parts = payload.get("parts") or []
    collected = [text] if text else []
    for part in parts:
        mime = (part.get("mimeType") or "").lower()
        if mime.startswith("text/plain") or mime.startswith("text/html") or not mime:
            pd = ((part.get("body") or {}).get("data"))
            if pd:
                collected.append(decode_b64url(pd))
        nested = part.get("parts")
        if nested:
            collected.append(extract_body(part))

    joined = "\n".join([c for c in collected if c]).strip()
    return re.sub(r"\s+", " ", joined)[:5000]


def parse_message(raw: Dict[str, Any]) -> ParsedMessage:
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []

    msg_id = str(raw.get("id") or "")
    thread_id = str(raw.get("threadId") or "")

    # gog may return either full Gmail API payload or simplified fields.
    sender = header_value(headers, "From") or str(raw.get("from") or "")
    subject = header_value(headers, "Subject") or str(raw.get("subject") or "")
    date_raw = header_value(headers, "Date") or str(raw.get("date") or "")
    snippet = raw.get("snippet") or ""
    body = extract_body(payload) or str(raw.get("body") or "") or snippet

    sender_name, sender_email = parse_sender(sender)

    date_iso = ""
    if raw.get("internalDate"):
        try:
            date_iso = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=timezone.utc).isoformat()
        except Exception:
            date_iso = ""
    if not date_iso and date_raw:
        try:
            parsed_dt = parsedate_to_datetime(date_raw)
            if parsed_dt is not None:
                date_iso = parsed_dt.astimezone(timezone.utc).isoformat()
        except Exception:
            date_iso = ""
        if not date_iso:
            # fallback for gog simplified date format: YYYY-MM-DD HH:MM
            try:
                date_iso = datetime.strptime(date_raw, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).isoformat()
            except Exception:
                date_iso = ""

    return ParsedMessage(
        message_id=msg_id,
        thread_id=thread_id,
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject or "(no subject)",
        date_raw=date_raw,
        date_iso=date_iso,
        snippet=(snippet or "")[:500],
        body=(body or "")[:5000],
    )


def likely_non_outreach(msg: ParsedMessage) -> Optional[str]:
    s = f"{msg.subject} {msg.snippet} {msg.body[:500]}".lower()
    sender = (msg.sender_email or "").lower()
    subject = (msg.subject or "").lower()

    if not sender or "@" not in sender:
        return "invalid_sender"
    if sender.endswith("@instructure.com") or sender.startswith("mailer-daemon"):
        return "system_notification"
    # self-sent messages are intentionally allowed (user wants sent-lead scanning)
    if sender.startswith(("notification@", "notifications@", "noreply@", "no-reply@")):
        return "notification_sender"
    if any(d in sender for d in ["@calendly.com", "@tally.so", "@luma-mail.com", "@verseoftheday.com"]):
        return "notification_domain"
    if any(k in s for k in ["unsubscribe", "view in browser", "notification settings", "canvas.ox.ac.uk", "delivery status notification"]):
        return "bulk_or_notification"
    if subject.startswith(("new event:", "verse of the day:", "new tally form submission")):
        return "automation_subject"
    if msg.subject.lower().startswith(("re:", "fw:", "fwd:")) and sender.startswith("aodhan."):
        return "forwarded_self"

    return None


def build_prompt(msg: ParsedMessage) -> Dict[str, Any]:
    return {
        "sender_name": msg.sender_name,
        "sender_email": msg.sender_email,
        "subject": msg.subject,
        "date": msg.date_iso or msg.date_raw,
        "snippet": msg.snippet,
        "body": msg.body,
        "task": "Classify whether this email is business outreach and summarize succinctly.",
        "rules": {
            "sentiment_values": ["interested", "neutral", "not_interested"],
            "intent_level_values": ["low", "medium", "high"],
            "outreach_type_values": [
                "cold_outreach",
                "follow_up",
                "partnership",
                "sales_pitch",
                "job_or_freelance",
                "meeting_request",
                "proposal",
                "other",
                "none",
            ],
        },
    }


def normalize_classification(data: Dict[str, Any]) -> Classification:
    return Classification(
        is_business_outreach=bool(data.get("is_business_outreach", False)),
        outreach_type=str(data.get("outreach_type", "none")),
        intent_level=str(data.get("intent_level", "low")),
        sentiment=str(data.get("sentiment", "neutral")),
        confidence=float(data.get("confidence", 0.0)),
        summary=str(data.get("summary", "")).strip(),
    )


def classify_message_openai(msg: ParsedMessage, api_key: str, model: str) -> Classification:
    prompt = build_prompt(msg)

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_business_outreach": {"type": "boolean"},
            "outreach_type": {
                "type": "string",
                "enum": [
                    "cold_outreach",
                    "follow_up",
                    "partnership",
                    "sales_pitch",
                    "job_or_freelance",
                    "meeting_request",
                    "proposal",
                    "other",
                    "none",
                ],
            },
            "intent_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "sentiment": {"type": "string", "enum": ["interested", "neutral", "not_interested"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "summary": {"type": "string"},
        },
        "required": [
            "is_business_outreach",
            "outreach_type",
            "intent_level",
            "sentiment",
            "confidence",
            "summary",
        ],
    }

    response = None
    for attempt in range(3):
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": "Return only valid JSON matching the schema."},
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "gmail_outreach_classification",
                        "schema": schema,
                        "strict": True,
                    },
                },
            },
            timeout=45,
        )

        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait_s = max(1, min(10, int(retry_after)))
            else:
                wait_s = 2 ** attempt
            time.sleep(wait_s)
            continue

        response.raise_for_status()
        break

    if response is None:
        raise RuntimeError("OpenAI request failed before response")

    content = response.json()["choices"][0]["message"]["content"]
    return normalize_classification(json.loads(content))


def classify_message_anthropic(msg: ParsedMessage, api_key: str, model: str) -> Classification:
    prompt = build_prompt(msg)

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 400,
            "temperature": 0,
            "system": "Return only strict JSON, no markdown, no prose.",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Classify this email for business outreach. Return JSON only with keys exactly: "
                        "is_business_outreach, outreach_type, intent_level, sentiment, confidence, summary. "
                        "Allowed outreach_type: cold_outreach, follow_up, partnership, sales_pitch, "
                        "job_or_freelance, meeting_request, proposal, other, none. "
                        "Allowed intent_level: low, medium, high. "
                        "Allowed sentiment: interested, neutral, not_interested. "
                        f"Email payload: {json.dumps(prompt)}"
                    ),
                }
            ],
        },
        timeout=45,
    )
    response.raise_for_status()
    content = response.json()["content"][0]["text"]
    return normalize_classification(json.loads(content))


def classify_message_gemini(msg: ParsedMessage, api_key: str, model: str) -> Classification:
    prompt = build_prompt(msg)
    instruction = (
        "Return ONLY valid JSON with keys exactly: "
        "is_business_outreach, outreach_type, intent_level, sentiment, confidence, summary. "
        "Allowed outreach_type: cold_outreach, follow_up, partnership, sales_pitch, "
        "job_or_freelance, meeting_request, proposal, other, none. "
        "Allowed intent_level: low, medium, high. "
        "Allowed sentiment: interested, neutral, not_interested."
    )

    model_name = model.replace("models/", "")
    response = None
    for attempt in range(4):
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                },
                "contents": [
                    {
                        "parts": [
                            {"text": instruction},
                            {"text": json.dumps(prompt)},
                        ]
                    }
                ],
            },
            timeout=45,
        )

        if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait_s = max(1, min(20, int(retry_after)))
            else:
                wait_s = 2 ** attempt
            time.sleep(wait_s)
            continue

        response.raise_for_status()
        break

    if response is None:
        raise RuntimeError("Gemini request failed before response")

    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return normalize_classification(json.loads(text))


def classify_message(msg: ParsedMessage, provider: str, api_key: str, model: str) -> Classification:
    provider = (provider or "openai").lower()
    if provider == "anthropic":
        return classify_message_anthropic(msg, api_key, model)
    if provider == "gemini":
        return classify_message_gemini(msg, api_key, model)
    return classify_message_openai(msg, api_key, model)


def map_status(c: Classification) -> str:
    if c.sentiment == "not_interested":
        return "lost"

    summary = (c.summary or "").lower()
    outreach_type = (c.outreach_type or "").lower()

    if "paying client" in summary or "invoice" in summary or "retainer" in summary or "signed" in summary:
        return "client"
    if "pilot" in summary or "trial accepted" in summary or "proof of concept" in summary:
        return "pilot"
    if "ready to proceed" in summary or "go ahead" in summary or (c.intent_level == "high" and c.sentiment == "interested"):
        return "closed"
    if outreach_type == "meeting_request" or (
        ("call" in summary or "meeting" in summary) and
        any(k in summary for k in ["schedule", "book", "availability", "calendar", "time works", "let's meet"])
    ):
        return "call_scheduled"
    if outreach_type in {"proposal", "partnership"} or "proposal" in summary or "scope" in summary:
        return "responded"
    if c.is_business_outreach and c.confidence >= 0.6:
        return "contacted"
    return "new"


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_email TEXT NOT NULL,
            message_id TEXT,
            thread_id TEXT,
            prospect_id INTEGER,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sender_email, message_id),
            UNIQUE(sender_email, thread_id)
        )
        """
    )
    conn.commit()
    return conn


def is_duplicate(conn: sqlite3.Connection, sender_email: str, message_id: str, thread_id: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM gmail_import_log
        WHERE sender_email = ?
          AND ((message_id IS NOT NULL AND message_id = ?) OR (thread_id IS NOT NULL AND thread_id = ?))
        LIMIT 1
        """,
        (sender_email, message_id, thread_id),
    ).fetchone()
    return row is not None


def maybe_upgrade_status(current: str, new: str) -> str:
    if current == "lost":
        return "lost"
    return new if STATUS_ORDER.get(new, 0) >= STATUS_ORDER.get(current, 0) else current


def upsert_prospect(
    conn: sqlite3.Connection,
    msg: ParsedMessage,
    cls: Classification,
    status: str,
    dry_run: bool,
) -> Tuple[str, Optional[int]]:
    if not msg.sender_email:
        return ("skip:no_sender_email", None)

    dup = is_duplicate(conn, msg.sender_email, msg.message_id, msg.thread_id)
    if dup:
        return ("skip:duplicate", None)

    existing = conn.execute("SELECT * FROM prospects WHERE lower(email) = lower(?) LIMIT 1", (msg.sender_email,)).fetchone()

    note = (
        f"[gmail-scan] date={msg.date_iso or msg.date_raw}; subject={msg.subject}; "
        f"sentiment={cls.sentiment}; confidence={cls.confidence:.2f}; summary={cls.summary}; "
        f"message_id={msg.message_id}; thread_id={msg.thread_id}"
    )

    if existing is None:
        if dry_run:
            return ("create(dry-run)", None)
        cur = conn.execute(
            """
            INSERT INTO prospects (name, company, email, linkedin, notes, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                msg.sender_name or msg.sender_email,
                None,
                msg.sender_email,
                None,
                note,
                status,
            ),
        )
        pid = cur.lastrowid
        conn.execute(
            "INSERT INTO gmail_import_log (sender_email, message_id, thread_id, prospect_id) VALUES (?, ?, ?, ?)",
            (msg.sender_email, msg.message_id or None, msg.thread_id or None, pid),
        )
        conn.commit()
        return ("create", pid)

    existing_status = existing["status"] or "new"
    next_status = maybe_upgrade_status(existing_status, status)
    merged_notes = (existing["notes"] or "")
    merged_notes = (merged_notes + "\n" + note).strip() if merged_notes else note

    if dry_run:
        return ("update(dry-run)", int(existing["id"]))

    conn.execute(
        """
        UPDATE prospects
        SET name = ?, notes = ?, status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            existing["name"] or msg.sender_name or msg.sender_email,
            merged_notes,
            next_status,
            existing["id"],
        ),
    )
    conn.execute(
        "INSERT INTO gmail_import_log (sender_email, message_id, thread_id, prospect_id) VALUES (?, ?, ?, ?)",
        (msg.sender_email, msg.message_id or None, msg.thread_id or None, existing["id"]),
    )
    conn.commit()
    return ("update", int(existing["id"]))


def print_audit(rows: List[Dict[str, Any]]) -> None:
    print("\nAudit log")
    print("-" * 120)
    print(f"{'sender':36} {'conf':>5} {'sentiment':14} {'status':14} {'action':18} subject")
    print("-" * 120)
    for r in rows:
        sender = (r['sender'] or '')[:36]
        subj = (r['subject'] or '').replace('\n', ' ')[:60]
        print(f"{sender:36} {r['confidence']:>5.2f} {r['sentiment']:14} {r['status']:14} {r['action']:18} {subj}")
    print("-" * 120)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Gmail and import likely outreach into outreach-tracker prospects")
    parser.add_argument("--max", type=int, default=30, help="Number of inbox messages to scan")
    parser.add_argument("--account", type=str, default=None, help="Optional gog account email")
    parser.add_argument("--query", type=str, default="in:inbox", help="Gmail query for gog search")
    parser.add_argument("--db", type=str, default=os.path.join(os.path.dirname(__file__), "..", "outreach.db"), help="Path to SQLite DB")
    parser.add_argument("--provider", type=str, default=DEFAULT_PROVIDER, choices=["openai", "anthropic", "gemini"], help="Classifier provider")
    parser.add_argument("--model", type=str, default=None, help="Model name (defaults based on provider)")
    parser.add_argument("--apply", action="store_true", help="Apply DB writes (default: dry-run)")
    parser.add_argument("--min-confidence", type=float, default=0.35, help="Minimum confidence to consider outreach import")
    args = parser.parse_args()

    dry_run = not args.apply
    provider = args.provider.lower()
    if provider == "anthropic":
        model = args.model or DEFAULT_ANTHROPIC_MODEL
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY is required for --provider anthropic", file=sys.stderr)
            return 2
    elif provider == "gemini":
        model = args.model or DEFAULT_GEMINI_MODEL
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("ERROR: GEMINI_API_KEY (or GOOGLE_API_KEY) is required for --provider gemini", file=sys.stderr)
            return 2
    else:
        model = args.model or DEFAULT_OPENAI_MODEL
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: OPENAI_API_KEY is required for --provider openai", file=sys.stderr)
            return 2

    try:
        raw_messages = run_gog_fetch(args.max, account=args.account, query=args.query)
    except Exception as e:
        print(f"ERROR: Gmail fetch failed: {e}", file=sys.stderr)
        return 3

    messages = [parse_message(m) for m in raw_messages]

    conn = get_db(os.path.abspath(args.db))

    audit_rows: List[Dict[str, Any]] = []
    processed = 0
    skipped_invalid_sender = 0
    skipped_prefilter = 0
    classify_errors = 0

    for msg in messages:
        # Skip clearly invalid senders
        if not msg.sender_email or "@" not in msg.sender_email:
            skipped_invalid_sender += 1
            audit_rows.append(
                {
                    "sender": msg.sender_email or "(missing)",
                    "confidence": 0.0,
                    "sentiment": "neutral",
                    "status": "new",
                    "action": "skip:invalid_sender",
                    "subject": msg.subject,
                }
            )
            continue

        prefilter_reason = likely_non_outreach(msg)
        if prefilter_reason:
            skipped_prefilter += 1
            audit_rows.append(
                {
                    "sender": msg.sender_email,
                    "confidence": 0.0,
                    "sentiment": "neutral",
                    "status": "new",
                    "action": f"skip:prefilter:{prefilter_reason}",
                    "subject": msg.subject,
                }
            )
            processed += 1
            continue

        try:
            cls = classify_message(msg, provider, api_key, model)
        except Exception as e:
            audit_rows.append(
                {
                    "sender": msg.sender_email,
                    "confidence": 0.0,
                    "sentiment": "neutral",
                    "status": "new",
                    "action": f"error:classify",
                    "subject": msg.subject,
                }
            )
            print(f"WARN: classification failed for {msg.sender_email}: {e}", file=sys.stderr)
            classify_errors += 1
            continue

        status = map_status(cls)

        if not cls.is_business_outreach or cls.confidence < args.min_confidence:
            action = "skip:not_outreach"
            audit_rows.append(
                {
                    "sender": msg.sender_email,
                    "confidence": cls.confidence,
                    "sentiment": cls.sentiment,
                    "status": status,
                    "action": action,
                    "subject": msg.subject,
                }
            )
            processed += 1
            continue

        action, _pid = upsert_prospect(conn, msg, cls, status, dry_run=dry_run)

        audit_rows.append(
            {
                "sender": msg.sender_email,
                "confidence": cls.confidence,
                "sentiment": cls.sentiment,
                "status": status,
                "action": action,
                "subject": msg.subject,
            }
        )
        processed += 1

    conn.close()

    print_audit(audit_rows)
    print(
        f"\nScanned={len(messages)} processed={processed} dry_run={dry_run} provider={provider} model={model} "
        f"query={args.query!r} skipped_invalid_sender={skipped_invalid_sender} "
        f"skipped_prefilter={skipped_prefilter} classify_errors={classify_errors}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
