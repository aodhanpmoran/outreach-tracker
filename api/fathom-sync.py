from http.server import BaseHTTPRequestHandler
import json
import os
import re
from datetime import datetime, timedelta, timezone
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
        'https://api.fathom.video/v1/calls',
        headers={'Authorization': f'Bearer {api_key}'},
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
  "full_name": "string or null",
  "company": "string or null",
  "email": "string or null",
  "relationship_type": "client" or "prospect" or "unknown",
  "confidence": "high" or "medium" or "low",
  "reasoning": "brief explanation"
}}

Classification rules:
- "client": Evidence of ongoing business relationship, past work together, active projects, invoices/payments
- "prospect": Discovery call, sales conversation, evaluating services, no prior work history
- "unknown": Cannot determine from context

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
        # Parse JSON from response (handle potential markdown wrapping)
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

    # Determine status based on relationship type
    relationship = llm_result.get('relationship_type', 'unknown')
    if relationship == 'client':
        status = 'client'
    elif relationship == 'prospect':
        status = 'contacted'  # They've had a call, so at least contacted
    else:
        status = 'new'

    try:
        result = supabase.table('prospects').insert({
            'name': llm_result['full_name'],
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

    # Log sync start
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

        # Handle different response formats
        if isinstance(meetings, dict):
            meetings_list = meetings.get('calls', meetings.get('data', []))
        else:
            meetings_list = meetings

        for meeting in meetings_list:
            stats['meetings_processed'] += 1

            # Get recording ID
            recording_id = meeting.get('id') or meeting.get('recording_id')
            if not recording_id:
                continue

            # Check if already exists
            existing = supabase.table('fathom_calls').select('id').eq('fathom_recording_id', str(recording_id)).execute()
            if existing.data:
                continue

            stats['meetings_new'] += 1

            title = meeting.get('title', 'Untitled Meeting')
            summary = meeting.get('summary', '')
            invitees = meeting.get('attendees', meeting.get('invitees', []))
            transcript = meeting.get('transcript', '')

            # Try to match to existing prospect
            prospect_id, confidence, matched_prospect = match_to_prospect(supabase, title, invitees)

            llm_extraction = None
            needs_review = False

            # If no match, try LLM extraction
            if not prospect_id:
                llm_extraction = extract_contact_with_llm(title, summary, transcript, invitees)

                if llm_extraction:
                    llm_confidence = llm_extraction.get('confidence', 'low')

                    if llm_confidence == 'high' and llm_extraction.get('full_name'):
                        # Auto-create the contact
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

            # Store the call
            call_data = {
                'fathom_recording_id': str(recording_id),
                'title': title,
                'summary': summary[:5000] if summary else None,
                'call_date': meeting.get('created_at') or meeting.get('start_time') or datetime.now(timezone.utc).isoformat(),
                'duration_minutes': meeting.get('duration_minutes') or meeting.get('duration'),
                'prospect_id': prospect_id,
                'auto_matched': prospect_id is not None and confidence != 'manual',
                'match_confidence': confidence,
                'needs_review': needs_review,
                'llm_extraction': llm_extraction,
                'raw_data': meeting
            }

            call_result = supabase.table('fathom_calls').insert(call_data).execute()

            # Store action items if present
            if call_result.data:
                call_id = call_result.data[0]['id']
                action_items = meeting.get('action_items', [])

                for item in action_items:
                    item_data = {
                        'fathom_call_id': call_id,
                        'description': item.get('text') or item.get('description') or str(item),
                        'assignee': item.get('assignee')
                    }
                    supabase.table('fathom_action_items').insert(item_data).execute()

        # Update sync log
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
    def do_POST(self):
        """Trigger a manual sync"""
        try:
            supabase = get_supabase()
            stats = sync_fathom_meetings(supabase, sync_type='api_manual', since_hours=24)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'stats': stats
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_GET(self):
        """Get sync status/logs"""
        try:
            supabase = get_supabase()
            response = supabase.table('fathom_sync_log').select('*').order('started_at', desc=True).limit(10).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
