#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, sqlite3, subprocess, os
from dataclasses import dataclass

try:
    from supabase import create_client
except Exception:
    create_client = None

SELF_EMAILS = {"aodhanpmoran@gmail.com", "aodhan.moran@conted.ox.ac.uk"}
NEG_PREFIX = ("no-reply@", "noreply@", "notification@", "notifications@", "donotreply@", "mailer-daemon@")
NEG_DOMAIN = ("@tally.so", "@calendly.com", "@luma-mail.com", "@verseoftheday.com", "@googlemail.com")
POS_KW = ["follow up", "following up", "conversation", "audience", "funnel", "collaborat", "proposal", "offer", "strategy", "help", "partnership", "can we", "would you"]
NEG_KW = ["invoice", "receipt", "shipment", "order", "pastoral", "workshop", "tutorial", "term", "conference schedule", "availability only"]

email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
name_email_re = re.compile(r'([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\.-]{1,60})\s*<\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\s*>')


@dataclass
class Candidate:
    email: str
    full_name: str
    latest_date: str
    subject: str
    score: int
    label: str
    reason: str
    summary: str


def run_gog(max_n: int, account: str):
    cmd = ["gog", "gmail", "messages", "search", "in:sent", "--max", str(max_n), "--json", "--include-body", "--no-input", "--account", account]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr or p.stdout)
    j = json.loads(p.stdout)
    return j.get("messages", []) if isinstance(j, dict) else j


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
    out = {}
    for m in name_email_re.finditer(text or ""):
        name = " ".join((m.group(1) or "").strip().split())
        email = (m.group(2) or "").strip().lower()
        if len(name) >= 2:
            out[email] = name
    return out


def chain_summary(subject: str, body: str, target_email: str, full_name: str) -> str:
    text = (body or "").replace("\r", "\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Keep only likely business-relevant current-thread lines; drop quoted metadata/noise.
    business_lines = []
    for ln in lines:
        low = ln.lower()
        if low.startswith(("on ", ">", "from:", "to:", "subject:", "sent:", "dear ", "hi ", "thanks", "thank you")):
            continue
        if any(k in low for k in [
            "offer", "proposal", "audience", "newsletter", "email", "lead", "revenue",
            "strategy", "funnel", "collabor", "partnership", "call", "meeting", "next step",
            "segment", "automation", "qualified", "conversion", "launch"
        ]):
            business_lines.append(ln)

    # Fallback: first meaningful non-header lines if keyword filter is too strict.
    if not business_lines:
        for ln in lines:
            low = ln.lower()
            if low.startswith(("on ", ">", "from:", "to:", "subject:", "sent:")):
                continue
            business_lines.append(ln)
            if len(business_lines) >= 4:
                break

    bullets = []
    if business_lines:
        bullets.append(f"- Context: {subject[:110]}")
        bullets.append(f"- Business focus: {business_lines[0][:180]}")
        if len(business_lines) > 1:
            bullets.append(f"- Signals: {business_lines[1][:180]}")
        if len(business_lines) > 2:
            bullets.append(f"- Next-step hint: {business_lines[2][:180]}")
    else:
        who = full_name or target_email
        bullets = [
            f"- Context: {subject[:110]}",
            f"- Contact: {who}",
            "- Business focus: limited signal in current extract.",
        ]

    # Ensure 2–4 lines.
    return "\n".join(bullets[:4])


def classify(email: str, text: str, existing_clients: set[str]):
    e = email.lower()
    if e in existing_clients:
        return ("existing_client", -5, "already client/pilot in tracker")
    if e in SELF_EMAILS:
        return ("admin_non_target", -5, "self email")
    if e.startswith(NEG_PREFIX) or any(d in e for d in NEG_DOMAIN):
        return ("admin_non_target", -4, "notification/system sender")

    t = text.lower()
    score = 0
    for k in POS_KW:
        if k in t:
            score += 2
    for k in NEG_KW:
        if k in t:
            score -= 2

    if score >= 3:
        return ("prospect_target", score, "commercial outreach language present")
    if score <= 0:
        return ("admin_non_target", score, "insufficient business outreach intent")
    return ("uncertain", score, "some outreach signals but weak")


def upsert_contacted_sqlite(db_path: str, email: str, full_name: str, note: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM prospects WHERE lower(email)=lower(?) LIMIT 1", (email,)).fetchone()
    forced_name = full_name or prettify_name(email)
    forced_status = 'new'  # Force into Target list

    if row is None:
        conn.execute(
            "INSERT INTO prospects (name,email,notes,status) VALUES (?,?,?,?)",
            (forced_name, email, note, forced_status)
        )
        conn.commit(); conn.close(); return "created"

    notes = (row['notes'] or '') + "\n" + note
    conn.execute(
        "UPDATE prospects SET name=?, notes=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (forced_name, notes.strip(), forced_status, row['id'])
    )
    conn.commit(); conn.close(); return "updated"


def upsert_contacted_supabase(email: str, full_name: str, note: str):
    if create_client is None:
        return "skipped:no_supabase_lib"
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return "skipped:no_supabase_env"

    sb = create_client(url, key)
    existing = sb.table('prospects').select('*').eq('email', email).limit(1).execute().data
    forced_name = full_name or prettify_name(email)
    forced_status = 'new'  # Force into Target list

    if not existing:
        sb.table('prospects').insert({
            'name': forced_name,
            'email': email,
            'notes': note,
            'status': forced_status,
        }).execute()
        return "created"

    row = existing[0]
    notes = ((row.get('notes') or '') + "\n" + note).strip()
    sb.table('prospects').update({
        'name': forced_name,
        'notes': notes,
        'status': forced_status,
    }).eq('id', row['id']).execute()
    return "updated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--account", required=True)
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "..", "outreach.db"))
    ap.add_argument("--import-email", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sync-supabase", action="store_true", help="Also upsert to Supabase when SUPABASE_URL/SUPABASE_KEY are set")
    args = ap.parse_args()

    msgs = run_gog(args.max, args.account)
    existing_clients = load_existing_clients(os.path.abspath(args.db))

    cand: dict[str, Candidate] = {}
    for m in msgs:
        subject = m.get("subject", "(no subject)")
        date = m.get("date", "")
        body = m.get("body", "")[:6000]
        text = f"{subject}\n{body}"
        names = extract_names(text)

        seen = set()
        for e in email_re.findall(text):
            e = e.lower()
            if e in seen:
                continue
            seen.add(e)

            label, score, reason = classify(e, text, existing_clients)
            full_name = names.get(e, prettify_name(e))
            summary = chain_summary(subject, body, e, full_name)

            prev = cand.get(e)
            if (prev is None) or (score > prev.score) or (date > prev.latest_date):
                cand[e] = Candidate(e, full_name, date, subject, score, label, reason, summary)

    targets = sorted(cand.values(), key=lambda c: (c.label != "prospect_target", -c.score, c.latest_date), reverse=False)

    print("email\tname\tlabel\tscore\tlatest_date\tsubject\treason")
    for c in targets[:40]:
        print(f"{c.email}\t{c.full_name}\t{c.label}\t{c.score}\t{c.latest_date}\t{c.subject[:80]}\t{c.reason}")

    if args.import_email:
        c = cand.get(args.import_email.lower())
        if not c:
            print(f"\nIMPORT: email not found in audit set: {args.import_email}")
            return
        if c.label != "prospect_target":
            print(f"\nIMPORT BLOCKED: {c.email} label={c.label} reason={c.reason}")
            return
        note = (
            f"[gmail_sent_target_audit] score={c.score}; label={c.label}; reason={c.reason}; "
            f"subject={c.subject}; date={c.latest_date}; summary={c.summary}"
        )
        if args.apply:
            action_sqlite = upsert_contacted_sqlite(os.path.abspath(args.db), c.email, c.full_name, note)
            print(f"\nIMPORT APPLIED (sqlite): {action_sqlite} {c.email} ({c.full_name})")
            if args.sync_supabase:
                action_supabase = upsert_contacted_supabase(c.email, c.full_name, note)
                print(f"IMPORT APPLIED (supabase): {action_supabase} {c.email}")
            print(f"SUMMARY: {c.summary}")
        else:
            print(f"\nIMPORT DRY-RUN: would import {c.email} ({c.full_name}) as contacted")
            print(f"SUMMARY: {c.summary}")


if __name__ == "__main__":
    main()
