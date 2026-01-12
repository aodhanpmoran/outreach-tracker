from http.server import BaseHTTPRequestHandler
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

from supabase import create_client
import requests


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)


def get_fathom_meetings(since_hours=2):
    """Fetch recent meetings from Fathom API"""
    api_key = os.environ.get("FATHOM_API_KEY")
    if not api_key:
        raise Exception("Missing FATHOM_API_KEY environment variable")

    since_date = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    response = requests.get(
        'https://api.fathom.ai/external/v1/meetings',
        headers={'X-Api-Key': api_key},
        params={
            'created_after': since_date.isoformat(),
        },
        timeout=30
    )

    if response.status_code == 429:
        raise Exception('Fathom API rate limit exceeded')

    response.raise_for_status()
    return response.json()


def parse_meeting_title(title):
    """Extract potential contact names from meeting title"""
    candidates = []

    # Pattern 1: "Name1/Name2" or "Name1 / Name2"
    if '/' in title:
        parts = [p.strip() for p in title.split('/')]
        candidates.extend(parts)

    # Pattern 2: "Call with Name - Company"
    match = re.match(r'(?:call|meeting|sync)\s+with\s+(.+?)(?:\s*-\s*(.+))?$', title, re.I)
    if match:
        candidates.append(match.group(1).strip())
        if match.group(2):
            candidates.append(match.group(2).strip())

    # Pattern 3: "Name <> Topic"
    if '<>' in title:
        parts = title.split('<>')
        candidates.append(parts[0].strip())

    # Clean up - remove common suffixes
    cleaned = []
    for c in candidates:
        c = re.sub(r'\s*(discovery|intro|followup|follow-up|call|meeting|sync|kickoff|kick-off|website|2024|2025|2026).*$', '', c, flags=re.I)
        c = c.strip()
        if c and len(c) > 1:
            cleaned.append(c)

    return cleaned


def match_to_prospect(supabase, title, invitees=None):
    """Try to match meeting to existing prospect by name/email"""
    candidates = parse_meeting_title(title)

    # Try name match
    for candidate in candidates:
        result = supabase.table('prospects').select('id,name,company,email').ilike('name', f'%{candidate}%').execute()
        if result.data and len(result.data) == 1:
            return result.data[0]['id'], 'high', result.data[0]

    # Try company match
    for candidate in candidates:
        result = supabase.table('prospects').select('id,name,company,email').ilike('company', f'%{candidate}%').execute()
        if result.data and len(result.data) == 1:
            return result.data[0]['id'], 'medium', result.data[0]

    # Try email match from invitees
    if invitees:
        for invitee in invitees:
            email = invitee.get('email') if isinstance(invitee, dict) else invitee
            if email:
                result = supabase.table('prospects').select('id,name,company,email').eq('email', email).execute()
                if result.data:
                    return result.data[0]['id'], 'high', result.data[0]

    return None, None, None


def get_meeting_invitees(meeting):
    invitees = meeting.get('attendees') or meeting.get('invitees') or meeting.get('calendar_invitees') or []
    if not invitees:
        raw = meeting.get('raw_data') or {}
        invitees = raw.get('calendar_invitees') or raw.get('invitees') or raw.get('attendees') or []
    return invitees


def get_recorded_by_email(meeting):
    recorded_by = meeting.get('recorded_by') or (meeting.get('raw_data') or {}).get('recorded_by') or {}
    return recorded_by.get('email')


def get_recorded_by_name(meeting):
    recorded_by = meeting.get('recorded_by') or (meeting.get('raw_data') or {}).get('recorded_by') or {}
    return recorded_by.get('name')


def get_excluded_emails():
    raw = os.environ.get('EXCLUDE_EMAILS') or ''
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def is_excluded_email(email, recorded_by_email, excluded_emails):
    if not email:
        return False
    email = email.lower()
    if recorded_by_email and email == recorded_by_email.lower():
        return True
    if email in excluded_emails:
        return True
    return False


def get_external_invitees(invitees, recorded_by_email):
    external = []
    recorded_by_email = (recorded_by_email or '').lower()
    excluded_emails = get_excluded_emails()
    for invitee in invitees:
        if not isinstance(invitee, dict):
            continue
        if invitee.get('is_external') is True:
            email = invitee.get('email')
            if email and is_excluded_email(email, recorded_by_email, excluded_emails):
                continue
            external.append(invitee)
            continue
        email = invitee.get('email')
        if not email:
            continue
        if is_excluded_email(email, recorded_by_email, excluded_emails):
            continue
        external.append(invitee)
    return external


def find_existing_prospect_by_email(supabase, email):
    if not email:
        return None
    result = supabase.table('prospects').select('id,name,company,email').eq('email', email).execute()
    if result.data:
        return result.data[0]
    return None


def find_existing_prospect_by_name(supabase, name):
    if not name:
        return None
    result = supabase.table('prospects').select('id,name,company,email').ilike('name', name).execute()
    if result.data and len(result.data) == 1:
        return result.data[0]
    return None


def title_case_name(value):
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    parts = []
    for token in re.split(r'\s+', value):
        if not token:
            continue
        subparts = re.split(r"([\-'\u2019])", token)
        rebuilt = ''
        for part in subparts:
            if part in ['-', "'", '’']:
                rebuilt += part
            elif part:
                rebuilt += part[:1].upper() + part[1:].lower()
        parts.append(rebuilt)
    return ' '.join(parts)


def derive_name_from_email(email):
    if not email:
        return None
    local = email.split('@')[0]
    local = local.split('+')[0]
    local = re.sub(r'[^a-zA-Z\s._-]', ' ', local)
    local = re.sub(r'[._-]+', ' ', local)
    local = re.sub(r'\s+', ' ', local).strip()
    if not local:
        return None
    return title_case_name(local)


def infer_name_from_transcript(transcript, recorded_by_name):
    if not transcript or not isinstance(transcript, str):
        return None
    recorded_by_name = (recorded_by_name or '').strip().lower()
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z][A-Za-z'\u2019\-\.]+(?:\s+[A-Za-z][A-Za-z'\u2019\-\.]+){0,2})\s*:", line)
        if match:
            candidate = match.group(1).strip()
            if recorded_by_name and recorded_by_name in candidate.lower():
                continue
            return title_case_name(candidate)
    return None


def extract_summary(meeting):
    raw = meeting.get('raw_data') or {}
    summary = (
        meeting.get('summary') or
        meeting.get('default_summary') or
        meeting.get('summary_text') or
        meeting.get('summary_markdown') or
        raw.get('summary') or
        raw.get('default_summary') or
        raw.get('summary_text') or
        raw.get('summary_markdown')
    )

    if not summary:
        action_items = meeting.get('action_items') or raw.get('action_items') or []
        if action_items:
            item_texts = []
            for item in action_items:
                if isinstance(item, dict):
                    text = item.get('text') or item.get('description')
                else:
                    text = str(item)
                if text:
                    item_texts.append(text.strip())
            if item_texts:
                summary = "Action items: " + "; ".join(item_texts[:5])

    if not summary:
        transcript = meeting.get('transcript') or raw.get('transcript')
        if isinstance(transcript, str) and transcript.strip():
            summary = transcript.strip().split('\\n')[0][:280]

    if not summary:
        title = meeting.get('title') or raw.get('meeting_title')
        if title:
            summary = f"Call recorded: {title}"

    return summary.strip() if isinstance(summary, str) and summary.strip() else None


def create_prospect_from_invitee(supabase, invitee, notes_reason):
    name = invitee.get('name') or invitee.get('full_name') or invitee.get('display_name')
    email = invitee.get('email')
    if not name:
        name = derive_name_from_email(email)
    name = title_case_name(name) if name else None

    existing = find_existing_prospect_by_email(supabase, email)
    if existing:
        return existing, False

    existing = find_existing_prospect_by_name(supabase, name)
    if existing:
        return existing, False

    if not name and not email:
        return None, False

    try:
        result = supabase.table('prospects').insert({
            'name': name or email,
            'company': None,
            'email': email,
            'status': 'contacted',
            'llm_created': False,
            'notes': notes_reason
        }).execute()

        if result.data:
            return result.data[0], True
    except Exception as e:
        print(f"Failed to create prospect from invitee: {e}")

    return None, False


def upsert_call_participant(supabase, call_id, prospect_id, source):
    try:
        supabase.table('fathom_call_participants').insert({
            'fathom_call_id': call_id,
            'prospect_id': prospect_id,
            'source': source
        }).execute()
    except Exception as e:
        # Ignore duplicates due to unique constraint
        if 'duplicate key value' in str(e).lower():
            return
        print(f"Failed to insert call participant: {e}")


def extract_participants(meeting, recorded_by_email):
    participants = []
    excluded_emails = get_excluded_emails()

    sources = [
        ('calendar_invitees', meeting.get('calendar_invitees')),
        ('attendees', meeting.get('attendees')),
        ('invitees', meeting.get('invitees'))
    ]
    raw = meeting.get('raw_data') or {}
    sources.extend([
        ('calendar_invitees', raw.get('calendar_invitees')),
        ('attendees', raw.get('attendees')),
        ('invitees', raw.get('invitees'))
    ])

    for source, items in sources:
        if not items:
            continue
        for invitee in items:
            if not isinstance(invitee, dict):
                continue
            email = invitee.get('email')
            if email and is_excluded_email(email, recorded_by_email, excluded_emails):
                continue
            name = invitee.get('name') or invitee.get('full_name') or invitee.get('display_name')
            participants.append({
                'name': name,
                'email': email,
                'source': source
            })

    recorded_by = meeting.get('recorded_by') or raw.get('recorded_by') or {}
    recorded_by_email = recorded_by.get('email')
    if recorded_by_email and not is_excluded_email(recorded_by_email, recorded_by_email, excluded_emails):
        participants.append({
            'name': recorded_by.get('name'),
            'email': recorded_by_email,
            'source': 'recorded_by'
        })

    transcript = meeting.get('transcript') or raw.get('transcript')
    if isinstance(transcript, str):
        transcript_name = infer_name_from_transcript(transcript, recorded_by.get('name'))
        if transcript_name:
            participants.append({
                'name': transcript_name,
                'email': None,
                'source': 'transcript'
            })

    # Deduplicate by email, then by name
    seen_email = set()
    seen_name = set()
    unique = []
    for participant in participants:
        email = (participant.get('email') or '').lower()
        name = title_case_name(participant.get('name') or '') or ''
        if email:
            if email in seen_email:
                continue
            seen_email.add(email)
        else:
            if name and name in seen_name:
                continue
            if name:
                seen_name.add(name)
        participant['name'] = name or participant.get('name')
        unique.append(participant)

    return unique


def derive_external_name_from_title(title, recorded_by_name):
    candidates = parse_meeting_title(title)
    if not candidates:
        return None
    if recorded_by_name:
        recorded_parts = [p for p in recorded_by_name.lower().split() if p]
        filtered = []
        for candidate in candidates:
            cand_lower = candidate.lower()
            if any(part in cand_lower for part in recorded_parts):
                continue
            filtered.append(candidate)
        candidates = filtered
    if len(candidates) == 1:
        return title_case_name(candidates[0])
    return None


def extract_contact_with_llm(title, summary, transcript_snippet, invitees):
    """Use OpenAI to extract contact info and classify relationship"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    invitee_emails = []
    if invitees:
        invitee_emails = [i.get('email') if isinstance(i, dict) else i for i in invitees if i]

    prompt = f"""Analyze this meeting and extract contact information.

Meeting Title: {title}
Summary: {summary or 'No summary available'}
Transcript Excerpt: {(transcript_snippet or '')[:2000]}
Invitee Emails: {', '.join(invitee_emails) if invitee_emails else 'None'}

Return JSON only (no markdown):
{{
  \"full_name\": \"string or null\",
  \"company\": \"string or null\",
  \"email\": \"string or null\",
  \"relationship_type\": \"client\" or \"prospect\" or \"unknown\",
  \"confidence\": \"high\" or \"medium\" or \"low\",
  \"reasoning\": \"brief explanation\"
}}

Classification rules:
- \"client\": Evidence of ongoing business relationship, past work together, active projects, invoices/payments
- \"prospect\": Discovery call, sales conversation, evaluating services, no prior work history
- \"unknown\": Cannot determine from context

Extract the external participant's name (not Aodhán)."""

    try:
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'gpt-4o-mini',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.1
            },
            timeout=30
        )
        response.raise_for_status()

        content = response.json()['choices'][0]['message']['content']
        content = content.strip()
        if content.startswith('```'):
            content = re.sub(r'^```(?:json)?\n?', '', content)
            content = re.sub(r'\n?```$', '', content)

        return json.loads(content)
    except Exception as e:
        print(f"LLM extraction failed: {e}")
        return None


def create_prospect_from_llm(supabase, llm_result):
    """Create a new prospect from LLM extraction results"""
    if not llm_result or not llm_result.get('full_name'):
        return None

    full_name = title_case_name(llm_result.get('full_name'))
    relationship = llm_result.get('relationship_type', 'unknown')
    if relationship == 'client':
        status = 'client'
    elif relationship == 'prospect':
        status = 'contacted'
    else:
        status = 'new'

    try:
        result = supabase.table('prospects').insert({
            'name': full_name or llm_result['full_name'],
            'company': llm_result.get('company'),
            'email': llm_result.get('email'),
            'status': status,
            'llm_created': True,
            'notes': f"Auto-created from Fathom call. LLM reasoning: {llm_result.get('reasoning', 'N/A')}"
        }).execute()

        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"Failed to create prospect: {e}")

    return None


def sync_fathom_meetings(supabase, sync_type='api_manual', since_hours=2):
    """Main sync function - fetches and processes Fathom meetings"""
    log_result = supabase.table('fathom_sync_log').insert({
        'sync_type': sync_type,
        'status': 'started'
    }).execute()
    log_id = log_result.data[0]['id'] if log_result.data else None

    stats = {
        'meetings_processed': 0,
        'meetings_new': 0,
        'contacts_created': 0,
        'needs_review_count': 0,
        'errors': []
    }

    try:
        meetings = get_fathom_meetings(since_hours)

        if isinstance(meetings, dict):
            meetings_list = meetings.get('items') or meetings.get('calls') or meetings.get('data') or []
        else:
            meetings_list = meetings or []

        for meeting in meetings_list:
            stats['meetings_processed'] += 1

            recording_id = meeting.get('recording_id') or meeting.get('id')
            if not recording_id:
                continue

            existing = supabase.table('fathom_calls').select('id, prospect_id').eq('fathom_recording_id', str(recording_id)).execute()
            existing_call = existing.data[0] if existing.data else None
            existing_linked = existing_call and existing_call.get('prospect_id') is not None

            title = meeting.get('title', 'Untitled Meeting')
            summary = extract_summary(meeting) or ''
            invitees = get_meeting_invitees(meeting)
            recorded_by_email = get_recorded_by_email(meeting)
            recorded_by_name = get_recorded_by_name(meeting)
            transcript = meeting.get('transcript') or (meeting.get('raw_data') or {}).get('transcript') or ''

            prospect_id, confidence, matched_prospect = match_to_prospect(supabase, title, invitees)

            llm_extraction = None
            needs_review = False

            if not prospect_id:
                external_invitees = get_external_invitees(invitees, recorded_by_email)
                if len(external_invitees) == 1:
                    prospect, created = create_prospect_from_invitee(
                        supabase,
                        external_invitees[0],
                        'Auto-created from Fathom calendar invitee.'
                    )
                    if prospect:
                        prospect_id = prospect['id']
                        confidence = 'auto_invitee'
                        if created:
                            stats['contacts_created'] += 1
                        needs_review = False

            if not prospect_id:
                inferred_name = infer_name_from_transcript(transcript, recorded_by_name)
                if inferred_name:
                    prospect, created = create_prospect_from_invitee(
                        supabase,
                        {'name': inferred_name, 'email': None},
                        'Auto-created from Fathom transcript.'
                    )
                    if prospect:
                        prospect_id = prospect['id']
                        confidence = 'auto_transcript'
                        if created:
                            stats['contacts_created'] += 1
                        needs_review = False

            if not prospect_id:
                inferred_name = derive_external_name_from_title(title, recorded_by_name)
                if inferred_name:
                    prospect, created = create_prospect_from_invitee(
                        supabase,
                        {'name': inferred_name, 'email': None},
                        'Auto-created from Fathom call title.'
                    )
                    if prospect:
                        prospect_id = prospect['id']
                        confidence = 'auto_title'
                        if created:
                            stats['contacts_created'] += 1
                        needs_review = False

            if not prospect_id:
                llm_extraction = extract_contact_with_llm(title, summary, transcript, invitees)

                if llm_extraction:
                    llm_confidence = llm_extraction.get('confidence', 'low')

                    if llm_confidence == 'high' and llm_extraction.get('full_name'):
                        new_prospect = create_prospect_from_llm(supabase, llm_extraction)
                        if new_prospect:
                            prospect_id = new_prospect['id']
                            confidence = 'llm_high'
                            stats['contacts_created'] += 1
                    else:
                        needs_review = True
                        stats['needs_review_count'] += 1
                else:
                    needs_review = True
                    stats['needs_review_count'] += 1

            if not prospect_id and not needs_review:
                needs_review = True
                stats['needs_review_count'] += 1

            participants = extract_participants(meeting, recorded_by_email)
            participant_ids = []

            for participant in participants:
                if not participant.get('email') and not participant.get('name'):
                    continue
                prospect, created = create_prospect_from_invitee(
                    supabase,
                    participant,
                    f"Auto-created from Fathom participants ({participant.get('source')})."
                )
                if prospect:
                    participant_ids.append(prospect['id'])
                    if created:
                        stats['contacts_created'] += 1

            if existing_call:
                update_data = {}
                if not existing_linked and prospect_id is not None:
                    update_data.update({
                        'prospect_id': prospect_id,
                        'auto_matched': confidence != 'manual',
                        'match_confidence': confidence,
                        'needs_review': needs_review
                    })
                elif not existing_linked:
                    update_data['needs_review'] = True

                if llm_extraction is not None:
                    update_data['llm_extraction'] = llm_extraction
                if summary:
                    update_data['summary'] = summary[:5000]
                if recorded_by_email:
                    update_data['recorded_by_email'] = recorded_by_email

                if update_data:
                    update_data['updated_at'] = datetime.now(timezone.utc).isoformat()
                    supabase.table('fathom_calls').update(update_data).eq('id', existing_call['id']).execute()

                for participant_id in participant_ids:
                    upsert_call_participant(supabase, existing_call['id'], participant_id, 'participants')
                continue

            stats['meetings_new'] += 1

            call_data = {
                'fathom_recording_id': str(recording_id),
                'title': title,
                'summary': summary[:5000] if summary else None,
                'call_date': meeting.get('created_at') or meeting.get('start_time') or datetime.now(timezone.utc).isoformat(),
                'duration_minutes': meeting.get('duration_minutes') or meeting.get('duration'),
                'recorded_by_email': recorded_by_email,
                'prospect_id': prospect_id,
                'auto_matched': prospect_id is not None and confidence != 'manual',
                'match_confidence': confidence,
                'needs_review': needs_review,
                'llm_extraction': llm_extraction,
                'raw_data': meeting
            }

            call_result = supabase.table('fathom_calls').insert(call_data).execute()

            if call_result.data:
                call_id = call_result.data[0]['id']
                for participant_id in participant_ids:
                    upsert_call_participant(supabase, call_id, participant_id, 'participants')
                action_items = meeting.get('action_items') or []

                for item in action_items:
                    item_data = {
                        'fathom_call_id': call_id,
                        'description': item.get('text') or item.get('description') or str(item),
                        'assignee': item.get('assignee')
                    }
                    supabase.table('fathom_action_items').insert(item_data).execute()

        if log_id:
            supabase.table('fathom_sync_log').update({
                'status': 'completed',
                'meetings_processed': stats['meetings_processed'],
                'meetings_new': stats['meetings_new'],
                'contacts_created': stats['contacts_created'],
                'needs_review_count': stats['needs_review_count'],
                'completed_at': datetime.now(timezone.utc).isoformat()
            }).eq('id', log_id).execute()

        return stats

    except Exception as e:
        stats['errors'].append(str(e))
        if log_id:
            supabase.table('fathom_sync_log').update({
                'status': 'failed',
                'errors': stats['errors'],
                'completed_at': datetime.now(timezone.utc).isoformat()
            }).eq('id', log_id).execute()
        raise


class handler(BaseHTTPRequestHandler):
    def _parse_query(self):
        return parse_qs(urlparse(self.path).query)

    def _get_endpoint(self, query):
        return query.get('endpoint', [None])[0]

    def _send_json(self, status, payload):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def _send_error(self, status, message):
        self._send_json(status, {'error': message})

    def _get_id(self, query):
        return query.get('id', [None])[0]

    def do_GET(self):
        try:
            query = self._parse_query()
            endpoint = self._get_endpoint(query)

            if endpoint == 'calls':
                return self._handle_calls_get(query)
            if endpoint == 'call':
                return self._handle_call_get(query)
            if endpoint == 'action-item':
                return self._handle_action_item_get(query)
            if endpoint == 'sync':
                return self._handle_sync_get()

            self._send_error(400, 'Unknown endpoint')
        except Exception as e:
            self._send_error(500, str(e))

    def do_POST(self):
        try:
            query = self._parse_query()
            endpoint = self._get_endpoint(query)

            if endpoint == 'sync':
                return self._handle_sync_post()

            self._send_error(405, 'Method not allowed')
        except Exception as e:
            self._send_error(500, str(e))

    def do_PATCH(self):
        try:
            query = self._parse_query()
            endpoint = self._get_endpoint(query)

            if endpoint == 'call':
                return self._handle_call_patch(query)
            if endpoint == 'action-item':
                return self._handle_action_item_patch(query)

            self._send_error(405, 'Method not allowed')
        except Exception as e:
            self._send_error(500, str(e))

    def do_DELETE(self):
        try:
            query = self._parse_query()
            endpoint = self._get_endpoint(query)

            if endpoint == 'call':
                return self._handle_call_delete(query)

            self._send_error(405, 'Method not allowed')
        except Exception as e:
            self._send_error(500, str(e))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _handle_calls_get(self, query):
        prospect_id = query.get('prospect_id', [None])[0]
        unmatched = query.get('unmatched', ['false'])[0].lower() == 'true'
        needs_review = query.get('needs_review', ['false'])[0].lower() == 'true'
        limit = int(query.get('limit', ['50'])[0])
        offset = int(query.get('offset', ['0'])[0])

        supabase = get_supabase()
        q = supabase.table('fathom_calls').select('*, fathom_action_items(id, description, completed, task_id)')

        if prospect_id:
            direct_calls = q.eq('prospect_id', prospect_id).execute().data or []
            seen_ids = {call.get('id') for call in direct_calls if call.get('id') is not None}

            participant_response = supabase.table('fathom_call_participants').select(
                'fathom_calls(*, fathom_action_items(id, description, completed, task_id))'
            ).eq('prospect_id', prospect_id).execute()

            for row in participant_response.data or []:
                call = row.get('fathom_calls')
                if isinstance(call, list):
                    for item in call:
                        if item.get('id') not in seen_ids:
                            direct_calls.append(item)
                            seen_ids.add(item.get('id'))
                elif isinstance(call, dict):
                    if call.get('id') not in seen_ids:
                        direct_calls.append(call)
                        seen_ids.add(call.get('id'))

            # Sort and paginate after merge
            direct_calls.sort(key=lambda c: c.get('call_date') or '', reverse=True)
            paged = direct_calls[offset:offset + limit]
            self._send_json(200, paged)
            return

        if unmatched:
            q = q.is_('prospect_id', 'null')

        if needs_review:
            q = q.eq('needs_review', True)

        response = q.order('call_date', desc=True).range(offset, offset + limit - 1).execute()
        self._send_json(200, response.data)

    def _handle_call_get(self, query):
        call_id = self._get_id(query)
        if not call_id:
            self._send_error(400, 'Missing id parameter')
            return

        supabase = get_supabase()
        response = supabase.table('fathom_calls').select(
            '*, fathom_action_items(id, description, assignee, completed, completed_at, task_id), prospects(id, name, company)'
        ).eq('id', call_id).execute()

        self._send_json(200, response.data[0] if response.data else None)

    def _handle_call_patch(self, query):
        call_id = self._get_id(query)
        if not call_id:
            self._send_error(400, 'Missing id parameter')
            return

        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data.decode())

        supabase = get_supabase()

        update_data = {}
        if 'prospect_id' in data:
            update_data['prospect_id'] = data['prospect_id']
            update_data['match_confidence'] = 'manual'
            update_data['auto_matched'] = False
            update_data['needs_review'] = False

        if 'needs_review' in data:
            update_data['needs_review'] = data['needs_review']

        if not update_data:
            self._send_error(400, 'No valid fields to update')
            return

        response = supabase.table('fathom_calls').update(update_data).eq('id', call_id).execute()
        self._send_json(200, response.data[0] if response.data else None)

    def _handle_call_delete(self, query):
        call_id = self._get_id(query)
        if not call_id:
            self._send_error(400, 'Missing id parameter')
            return

        supabase = get_supabase()
        supabase.table('fathom_calls').delete().eq('id', call_id).execute()
        self._send_json(200, {'success': True})

    def _handle_action_item_get(self, query):
        action_id = query.get('id', [None])[0]
        call_id = query.get('call_id', [None])[0]

        supabase = get_supabase()

        if action_id:
            response = supabase.table('fathom_action_items').select('*').eq('id', action_id).execute()
            result = response.data[0] if response.data else None
        elif call_id:
            response = supabase.table('fathom_action_items').select('*').eq('fathom_call_id', call_id).execute()
            result = response.data
        else:
            response = supabase.table('fathom_action_items').select(
                '*, fathom_calls(id, title, call_date, prospect_id)'
            ).eq('completed', False).order('created_at', desc=True).limit(50).execute()
            result = response.data

        self._send_json(200, result)

    def _handle_action_item_patch(self, query):
        action_id = self._get_id(query)
        if not action_id:
            self._send_error(400, 'Missing id parameter')
            return

        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data.decode())

        supabase = get_supabase()

        update_data = {}
        if 'completed' in data:
            update_data['completed'] = data['completed']
            if data['completed']:
                update_data['completed_at'] = datetime.now(timezone.utc).isoformat()
            else:
                update_data['completed_at'] = None

        if 'task_id' in data:
            update_data['task_id'] = data['task_id']

        if not update_data:
            self._send_error(400, 'No valid fields to update')
            return

        response = supabase.table('fathom_action_items').update(update_data).eq('id', action_id).execute()
        self._send_json(200, response.data[0] if response.data else None)

    def _handle_sync_post(self):
        query = self._parse_query()
        since_hours = 24
        raw_hours = query.get('since_hours', [None])[0]
        raw_days = query.get('since_days', [None])[0] or query.get('days', [None])[0]

        try:
            if raw_days is not None:
                since_hours = float(raw_days) * 24
            elif raw_hours is not None:
                since_hours = float(raw_hours)
        except ValueError:
            since_hours = 24

        if since_hours <= 0:
            since_hours = 24

        since_hours = min(since_hours, 24 * 31)

        supabase = get_supabase()
        stats = sync_fathom_meetings(supabase, sync_type='api_manual', since_hours=since_hours)
        self._send_json(200, {'success': True, 'stats': stats, 'since_hours': since_hours})

    def _handle_sync_get(self):
        supabase = get_supabase()
        response = supabase.table('fathom_sync_log').select('*').order('started_at', desc=True).limit(10).execute()
        self._send_json(200, response.data)
