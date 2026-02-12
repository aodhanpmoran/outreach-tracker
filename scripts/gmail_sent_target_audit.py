#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    from supabase import create_client
except Exception:
    create_client = None

SELF_EMAILS = {"aodhanpmoran@gmail.com", "aodhan.moran@conted.ox.ac.uk"}
KNOWN_NEG_PREFIX = (
    "no-reply@",
    "noreply@",
    "notification@",
    "notifications@",
    "donotreply@",
    "mailer-daemon@",
)
KNOWN_NEG_DOMAIN = (
    "@tally.so",
    "@calendly.com",
    "@luma-mail.com",
    "@verseoftheday.com",
    "@googlemail.com",
)
ROLE_LOCAL_PARTS = {
    "info",
    "support",
    "hello",
    "contact",
    "admin",
    "team",
    "office",
    "billing",
    "accounts",
    "notifications",
}
DEFAULT_PREFER_TITLES = [
    "founder",
    "ceo",
    "head of",
    "director",
    "marketing",
    "growth",
]
DEFAULT_SKIP_KEYWORDS = [
    "invoice",
    "receipt",
    "shipment",
    "order",
    "verse of the day",
    "canvas.ox.ac.uk",
    "delivery status notification",
]
AUDIT_NOTE_PREFIX = "[gmail_sent_target_audit]"

email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
name_email_re = re.compile(r'([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\.-]{1,60})\s*<\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\s*>')
quoted_reply_re = re.compile(r"(?:on .*?wrote:|from:\s*.*?<([^>]+@[^>]+)>|from:\s*([^\s]+@[^\s]+))", re.IGNORECASE)
duration_re = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minute|minutes)\b", re.IGNORECASE)


@dataclass
class LearningConfig:
    skip_domains: list[str]
    prefer_titles: list[str]
    skip_keywords: list[str]
    min_exchanges: int = 1
    max_days_between: int = 60
    max_attendees: int = 10
    min_duration_minutes: int = 15


@dataclass
class ContactSignal:
    email: str
    full_name: str
    sent_count: int = 0
    thread_ids: set[str] = field(default_factory=set)
    latest_date: str = ""
    latest_ts: float = 0.0
    latest_subject: str = ""
    latest_body: str = ""
    inferred_reply_quotes: int = 0
    attendee_estimate: int = 1
    max_duration_minutes: int = 0
    first_ts: float = 0.0
    evidence_text: str = ""
    meeting_count: int = 0
    last_meeting_date: str = ""
    last_meeting_ts: float = 0.0
    cross_signal: bool = False


@dataclass
class Candidate:
    email: str
    full_name: str
    latest_date: str
    latest_ts: float
    subject: str
    label: str
    reason: str
    reason_codes: list[str]
    summary: str
    exchange_estimate: int
    confidence_bucket: str
    meeting_count: int = 0
    last_meeting_date: str = ""
    cross_signal: bool = False
    score_boost: int = 0


def parse_date_ts(date_value: str) -> float:
    if not date_value:
        return 0.0

    value = date_value.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def run_gog(max_n: int, account: str):
    cmd = [
        "gog", "gmail", "messages", "search", "in:sent", "--max", str(max_n),
        "--json", "--include-body", "--no-input", "--account", account,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())

    payload = json.loads(proc.stdout)
    if isinstance(payload, dict):
        if isinstance(payload.get("messages"), list):
            return payload["messages"]
        if isinstance(payload.get("data"), list):
            return payload["data"]
        return []
    return payload if isinstance(payload, list) else []


def run_gog_calendar(account: str, days_back: int = 60, max_n: int = 300) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(1, days_back))
    cmd = [
        "gog", "calendar", "events", "primary",
        "--from", start.isoformat(),
        "--to", now.isoformat(),
        "--max", str(max_n),
        "--json", "--no-input", "--account", account,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())

    payload = json.loads(proc.stdout)
    if isinstance(payload, dict):
        if isinstance(payload.get("events"), list):
            return payload["events"]
        if isinstance(payload.get("data"), list):
            return payload["data"]
        return []
    return payload if isinstance(payload, list) else []


def event_datetime(event: dict[str, Any], key: str) -> tuple[str, float]:
    node = event.get(key) or {}
    raw = str(node.get("dateTime") or node.get("date") or "")
    return raw, parse_date_ts(raw)


def event_duration_minutes(event: dict[str, Any]) -> int:
    _, s = event_datetime(event, "start")
    _, e = event_datetime(event, "end")
    if s and e and e > s:
        return int((e - s) / 60)
    return 0


def valid_meeting_event(event: dict[str, Any], min_duration_minutes: int, min_attendees: int = 1, max_attendees: int = 10) -> bool:
    attendees = event.get("attendees") or []
    if not isinstance(attendees, list):
        attendees = []
    attendee_count = len(attendees)
    if attendee_count < min_attendees or attendee_count > max_attendees:
        return False
    duration = event_duration_minutes(event)
    if duration < min_duration_minutes:
        return False
    status = str(event.get("status") or "").lower()
    if status == "cancelled":
        return False
    return True


def calendar_contact_signals(events: list[dict[str, Any]], known_emails: set[str], min_duration_minutes: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ev in events:
        if not valid_meeting_event(ev, min_duration_minutes=min_duration_minutes):
            continue

        raw_start, start_ts = event_datetime(ev, "start")
        attendees = ev.get("attendees") or []
        for a in attendees:
            email = str(a.get("email") or "").strip().lower()
            if not email or email not in known_emails:
                continue
            display = str(a.get("displayName") or "").strip()
            slot = out.get(email)
            if slot is None:
                slot = {"meeting_count": 0, "last_meeting_date": "", "last_meeting_ts": 0.0, "names": set()}
                out[email] = slot

            slot["meeting_count"] += 1
            if display:
                slot["names"].add(display)
            if start_ts >= float(slot["last_meeting_ts"]):
                slot["last_meeting_ts"] = start_ts
                slot["last_meeting_date"] = raw_start

    return out


def load_learning_config(path: str) -> LearningConfig:
    defaults = LearningConfig(
        skip_domains=[],
        prefer_titles=list(DEFAULT_PREFER_TITLES),
        skip_keywords=list(DEFAULT_SKIP_KEYWORDS),
        min_exchanges=1,
        max_days_between=60,
        max_attendees=10,
        min_duration_minutes=15,
    )
    if not os.path.exists(path):
        return defaults

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return LearningConfig(
        skip_domains=[str(x).lower() for x in raw.get("skip_domains", defaults.skip_domains)],
        prefer_titles=[str(x).lower() for x in raw.get("prefer_titles", defaults.prefer_titles)],
        skip_keywords=[str(x).lower() for x in raw.get("skip_keywords", defaults.skip_keywords)],
        min_exchanges=max(0, int(raw.get("min_exchanges", defaults.min_exchanges))),
        max_days_between=max(1, int(raw.get("max_days_between", defaults.max_days_between))),
        max_attendees=max(1, int(raw.get("max_attendees", defaults.max_attendees))),
        min_duration_minutes=max(1, int(raw.get("min_duration_minutes", defaults.min_duration_minutes))),
    )


def load_existing_clients(db_path: str) -> set[str]:
    if not os.path.exists(db_path):
        return set()
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT lower(email) FROM prospects WHERE email IS NOT NULL AND status IN ('client','pilot')").fetchall()
    conn.close()
    return {r[0] for r in rows if r[0]}


def prettify_name(email: str) -> str:
    local = email.split("@", 1)[0]
    parts = re.split(r"[._-]+", local)
    return " ".join(p.capitalize() for p in parts if p)


def extract_names(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in name_email_re.finditer(text or ""):
        name = " ".join((m.group(1) or "").strip().split())
        email = (m.group(2) or "").strip().lower()
        if len(name) >= 2:
            out[email] = name
    return out


def estimate_attendees(text: str) -> int:
    # rough proxy from unique email mentions in thread extract
    unique_emails = {e.lower() for e in email_re.findall(text or "")}
    return max(1, len(unique_emails))


def extract_duration_minutes(text: str) -> int:
    vals = [int(m.group(1)) for m in duration_re.finditer(text or "")]
    return max(vals) if vals else 0


def extract_inferred_reply_count(text: str, target_email: str) -> int:
    count = 0
    low_target = (target_email or "").lower()
    for m in quoted_reply_re.finditer(text or ""):
        g = (m.group(1) or m.group(2) or "").strip().lower()
        if not g:
            # generic "On ... wrote:" still indicates prior exchange in-thread
            count += 1
            continue
        if low_target and low_target in g:
            count += 1
    return count


def chain_summary(subject: str, body: str, target_email: str, full_name: str, reason_codes: list[str], exchange_estimate: int, confidence_bucket: str) -> str:
    # Harsh note rules: only keep business signal from the latest visible turn.
    text = (body or "").replace("\r", "\n")
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Use only pre-quote/latest segment (before historical quote headers).
    latest_segment = []
    for ln in raw_lines:
        low = ln.lower()
        if low.startswith(("on ", "from:", "to:", "subject:", "sent:")):
            break
        if ln.startswith(">"):
            break
        latest_segment.append(ln)

    if not latest_segment:
        latest_segment = raw_lines[:8]

    # Business-topic extraction from subject.
    topic = subject or "(no subject)"
    topic = re.sub(r"^(re|fw|fwd):\s*", "", topic, flags=re.IGNORECASE).strip()

    business_kws = [
        "proposal", "offer", "audience", "strategy", "funnel", "revenue", "lead",
        "partnership", "collaboration", "call", "meeting", "next step", "scope",
        "automation", "conversion", "launch", "distribution", "donor"
    ]

    # Pick strongest business line from latest segment.
    best = ""
    for ln in latest_segment:
        low = ln.lower()
        if low.startswith(("dear ", "hi ", "thanks", "thank you", "best", "regards")):
            continue
        if any(k in low for k in business_kws):
            best = ln
            break

    if not best:
        for ln in latest_segment:
            low = ln.lower()
            if low.startswith(("dear ", "hi ", "thanks", "thank you", "best", "regards")):
                continue
            if len(ln) > 18:
                best = ln
                break

    if not best:
        best = "No strong business line detected in latest turn."

    who = full_name or target_email
    signal_text = ", ".join(reason_codes[:4]) if reason_codes else "basic engagement signals"
    return (
        f"Most recent email thread with {who} is about {topic[:90]}. "
        f"The main business point is: {best[:180]}. "
        f"Current engagement appears {confidence_bucket} confidence with roughly {exchange_estimate} exchanges, "
        f"based on {signal_text}."
    )


def classify_stage1(email: str, text: str, existing_clients: set[str], cfg: LearningConfig) -> tuple[Optional[str], list[str]]:
    e = email.lower()
    reasons: list[str] = []

    if e in existing_clients:
        return "existing_client", ["existing_client"]
    if e in SELF_EMAILS:
        return "admin_non_target", ["self_email"]

    local = e.split("@", 1)[0] if "@" in e else e
    if local in ROLE_LOCAL_PARTS:
        return "admin_non_target", ["role_inbox"]

    if e.startswith(KNOWN_NEG_PREFIX):
        return "admin_non_target", ["notification_prefix"]

    if any(d in e for d in KNOWN_NEG_DOMAIN):
        return "admin_non_target", ["notification_domain"]

    domain = e.split("@", 1)[1] if "@" in e else ""
    if domain and domain in set(cfg.skip_domains):
        return "admin_non_target", ["learning_skip_domain"]

    t = (text or "").lower()
    for kw in cfg.skip_keywords:
        if kw and kw in t:
            return "admin_non_target", ["learning_skip_keyword"]

    return None, reasons


def classify_stage2(signal: ContactSignal, cfg: LearningConfig) -> tuple[str, list[str], int, str]:
    reason_codes: list[str] = []

    exchange_estimate = min(signal.sent_count, signal.inferred_reply_quotes)
    if signal.inferred_reply_quotes == 0 and signal.sent_count >= 2:
        # weak fallback: multiple sents in same contact can indicate continuity, but low confidence
        exchange_estimate = 1
        reason_codes.append("exchange_inferred_from_repeat_sent")

    if exchange_estimate >= cfg.min_exchanges:
        reason_codes.append("exchange_threshold_met")
    else:
        reason_codes.append("exchange_below_threshold")

    if signal.first_ts and signal.latest_ts:
        days_between = int(max(0, (signal.latest_ts - signal.first_ts) / 86400))
    else:
        days_between = 0

    if days_between <= cfg.max_days_between:
        reason_codes.append("recency_window_ok")
    else:
        reason_codes.append("recency_window_too_wide")

    if signal.attendee_estimate <= cfg.max_attendees:
        reason_codes.append("attendee_count_ok")
    else:
        reason_codes.append("attendee_count_too_high")

    if signal.max_duration_minutes and signal.max_duration_minutes < cfg.min_duration_minutes:
        reason_codes.append("duration_too_short")

    text_l = (signal.evidence_text or "").lower()
    if any(t in text_l for t in cfg.prefer_titles):
        reason_codes.append("preferred_title_signal")

    pass_gate = (
        exchange_estimate >= cfg.min_exchanges
        and days_between <= cfg.max_days_between
        and signal.attendee_estimate <= cfg.max_attendees
        and "duration_too_short" not in reason_codes
    )

    if pass_gate:
        label = "prospect_target"
    elif "exchange_below_threshold" in reason_codes:
        label = "uncertain"
    else:
        label = "admin_non_target"

    if label == "prospect_target" and "preferred_title_signal" in reason_codes and exchange_estimate >= max(2, cfg.min_exchanges):
        confidence = "high"
    elif label == "prospect_target":
        confidence = "medium"
    else:
        confidence = "low"

    return label, reason_codes, exchange_estimate, confidence


def merged_audit_notes(existing_notes: Optional[str], new_note: str) -> str:
    cleaned_lines = [
        ln for ln in (existing_notes or "").splitlines()
        if ln.strip() and not ln.strip().startswith(AUDIT_NOTE_PREFIX)
    ]
    cleaned_lines.append(new_note)
    return "\n".join(cleaned_lines).strip()


def build_audit_note(c: Candidate) -> str:
    # Learning rule: notes must be plain-English and minimal (no metadata dump).
    # Keep only the first sentence of the summary.
    first_sentence = (c.summary or "").split(".")[0].strip()
    if first_sentence:
        return first_sentence + "."
    return "No clear business summary available from the most recent email thread."


def upsert_contacted_sqlite(db_path: str, c: Candidate):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM prospects WHERE lower(email)=lower(?) ORDER BY id ASC", (c.email,)).fetchall()
    forced_name = c.full_name or prettify_name(c.email)
    forced_status = "new"
    note = build_audit_note(c)

    if not rows:
        conn.execute(
            "INSERT INTO prospects (name,email,notes,status) VALUES (?,?,?,?)",
            (forced_name, c.email, note, forced_status),
        )
        conn.commit()
        conn.close()
        return "created"

    keeper = rows[0]
    merged_notes = []
    for r in rows:
        if r["notes"]:
            merged_notes.append(r["notes"])
    merged_notes.append(note)
    final_notes = merged_audit_notes("\n".join(merged_notes), note)

    conn.execute(
        "UPDATE prospects SET name=?, notes=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (keeper["name"] or forced_name, final_notes, forced_status, keeper["id"]),
    )

    for r in rows[1:]:
        conn.execute("DELETE FROM prospects WHERE id=?", (r["id"],))

    conn.commit()
    conn.close()
    return "updated" if len(rows) == 1 else f"merged({len(rows)})"


def upsert_contacted_supabase(c: Candidate):
    if create_client is None:
        return "skipped:no_supabase_lib"

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return "skipped:no_supabase_env"

    sb = create_client(url, key)
    existing = sb.table("prospects").select("*").eq("email", c.email).execute().data or []
    forced_name = c.full_name or prettify_name(c.email)
    forced_status = "new"
    note = build_audit_note(c)

    if not existing:
        sb.table("prospects").insert({
            "name": forced_name,
            "email": c.email,
            "notes": note,
            "status": forced_status,
        }).execute()
        return "created"

    existing_sorted = sorted(existing, key=lambda r: r.get("id", 0))
    keeper = existing_sorted[0]
    merged_notes = []
    for r in existing_sorted:
        if r.get("notes"):
            merged_notes.append(r.get("notes"))
    merged_notes.append(note)

    sb.table("prospects").update({
        "name": keeper.get("name") or forced_name,
        "notes": merged_audit_notes("\n".join(merged_notes), note),
        "status": forced_status,
    }).eq("id", keeper["id"]).execute()

    for r in existing_sorted[1:]:
        sb.table("prospects").delete().eq("id", r["id"]).execute()

    return "updated" if len(existing_sorted) == 1 else f"merged({len(existing_sorted)})"


def reason_string(label: str, reason_codes: list[str]) -> str:
    if label == "existing_client":
        return "already client/pilot in tracker"
    if not reason_codes:
        return "no_reason"
    return ", ".join(reason_codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--account", required=True)
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "..", "outreach.db"))
    ap.add_argument("--learning-config", default=os.path.join(os.path.dirname(__file__), "learning.json"))
    ap.add_argument("--import-email", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sync-supabase", action="store_true", help="Also upsert to Supabase when SUPABASE_URL/SUPABASE_KEY are set")
    args = ap.parse_args()

    msgs = run_gog(args.max, args.account)
    existing_clients = load_existing_clients(os.path.abspath(args.db))
    cfg = load_learning_config(os.path.abspath(args.learning_config))

    signals: dict[str, ContactSignal] = {}

    for m in msgs:
        subject = m.get("subject", "(no subject)")
        date = m.get("date", "")
        latest_ts = parse_date_ts(date)
        body = (m.get("body", "") or "")[:10000]
        text = f"{subject}\n{body}"
        names = extract_names(text)
        thread_id = str(m.get("threadId") or m.get("thread_id") or "")

        seen = set()
        for email in email_re.findall(text):
            email = email.lower()
            if email in seen:
                continue
            seen.add(email)

            sig = signals.get(email)
            if sig is None:
                sig = ContactSignal(email=email, full_name=names.get(email, prettify_name(email)))
                signals[email] = sig

            sig.sent_count += 1
            if thread_id:
                sig.thread_ids.add(thread_id)
            sig.inferred_reply_quotes += extract_inferred_reply_count(text, email)
            sig.attendee_estimate = max(sig.attendee_estimate, estimate_attendees(text))
            sig.max_duration_minutes = max(sig.max_duration_minutes, extract_duration_minutes(text))
            sig.evidence_text = (sig.evidence_text + "\n" + text[:1000]).strip()

            if not sig.first_ts or (latest_ts and latest_ts < sig.first_ts):
                sig.first_ts = latest_ts

            if latest_ts >= sig.latest_ts:
                sig.latest_ts = latest_ts
                sig.latest_date = date
                sig.latest_subject = subject
                sig.latest_body = body
                sig.full_name = names.get(email, sig.full_name)

    calendar_warning = ""
    try:
        cal_events = run_gog_calendar(args.account, days_back=60, max_n=max(200, args.max * 5))
        cal_signals = calendar_contact_signals(cal_events, set(signals.keys()), cfg.min_duration_minutes)
    except Exception as e:
        cal_signals = {}
        calendar_warning = str(e)

    for email, cs in cal_signals.items():
        sig = signals.get(email)
        if sig is None:
            continue
        sig.meeting_count = int(cs.get("meeting_count") or 0)
        sig.last_meeting_date = str(cs.get("last_meeting_date") or "")
        sig.last_meeting_ts = float(cs.get("last_meeting_ts") or 0.0)
        sig.cross_signal = sig.sent_count > 0 and sig.meeting_count > 0
        names = cs.get("names") or set()
        if not sig.full_name and names:
            sig.full_name = sorted(list(names))[0]

    candidates: dict[str, Candidate] = {}

    for email, sig in signals.items():
        stage1_label, reason_codes = classify_stage1(email, sig.evidence_text, existing_clients, cfg)

        if stage1_label is not None:
            label = stage1_label
            exchange_estimate = min(sig.sent_count, sig.inferred_reply_quotes)
            confidence_bucket = "low" if label != "existing_client" else "medium"
            codes = reason_codes
        else:
            label, codes, exchange_estimate, confidence_bucket = classify_stage2(sig, cfg)

        score_boost = 0
        if sig.cross_signal:
            codes = list(codes) + ["cross_signal_boost"]
            score_boost = 1
            if label == "prospect_target" and confidence_bucket == "medium":
                confidence_bucket = "high"

        reason = reason_string(label, codes)
        summary = chain_summary(
            sig.latest_subject,
            sig.latest_body,
            email,
            sig.full_name,
            codes,
            exchange_estimate,
            confidence_bucket,
        )

        candidates[email] = Candidate(
            email=email,
            full_name=sig.full_name,
            latest_date=sig.latest_date,
            latest_ts=sig.latest_ts,
            subject=sig.latest_subject,
            label=label,
            reason=reason,
            reason_codes=codes,
            summary=summary,
            exchange_estimate=exchange_estimate,
            confidence_bucket=confidence_bucket,
            meeting_count=sig.meeting_count,
            last_meeting_date=sig.last_meeting_date,
            cross_signal=sig.cross_signal,
            score_boost=score_boost,
        )

    targets = sorted(
        candidates.values(),
        key=lambda c: (c.label != "prospect_target", -c.score_boost, c.confidence_bucket != "high", -c.exchange_estimate, -c.latest_ts),
    )

    print("email\tname\tlabel\texchange_estimate\tmeeting_count\tlast_meeting_date\tcross_signal\tconfidence_bucket\treason_codes\tlatest_date\tsubject")
    for c in targets[:60]:
        print(
            f"{c.email}\t{c.full_name}\t{c.label}\t{c.exchange_estimate}\t{c.meeting_count}\t{c.last_meeting_date}\t{str(c.cross_signal).lower()}\t{c.confidence_bucket}\t"
            f"{','.join(c.reason_codes)}\t{c.latest_date}\t{c.subject[:80]}"
        )

    if calendar_warning:
        print(f"\nCALENDAR WARNING: unavailable, continuing with email-only signals ({calendar_warning})")

    if not args.import_email:
        return

    c = candidates.get(args.import_email.lower())
    if not c:
        print(f"\nIMPORT: email not found in audit set: {args.import_email}")
        return

    if c.label != "prospect_target":
        print(f"\nIMPORT BLOCKED: {c.email} label={c.label} reason={c.reason}")
        return

    if args.apply:
        action_sqlite = upsert_contacted_sqlite(os.path.abspath(args.db), c)
        print(f"\nIMPORT APPLIED (sqlite): {action_sqlite} {c.email} ({c.full_name})")
        if args.sync_supabase:
            action_supabase = upsert_contacted_supabase(c)
            print(f"IMPORT APPLIED (supabase): {action_supabase} {c.email}")
        print(f"SUMMARY: {c.summary}")
    else:
        print(f"\nIMPORT DRY-RUN: would import {c.email} ({c.full_name}) as contacted")
        print(f"SUMMARY: {c.summary}")


if __name__ == "__main__":
    main()
