from http.server import BaseHTTPRequestHandler
import json
import os
from urllib.parse import urlparse, parse_qs
from supabase import create_client

ACTIVE_DEAL_STATUSES = {'contacted', 'responded', 'call_scheduled', 'closed'}
REQUIRED_ACTIVE_FIELDS = ['next_action', 'next_action_due_date', 'action_channel', 'action_objective']


def validate_payload(data):
    status = data.get('status', 'new')
    if status in ACTIVE_DEAL_STATUSES:
        missing = [field for field in REQUIRED_ACTIVE_FIELDS if not str(data.get(field) or '').strip()]
        if missing:
            return f"Missing required fields for active deal: {', '.join(missing)}"
    return None

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

class handler(BaseHTTPRequestHandler):
    def get_id(self):
        query = parse_qs(urlparse(self.path).query)
        return query.get('id', [None])[0]

    def do_GET(self):
        try:
            prospect_id = self.get_id()
            if not prospect_id:
                self.send_error(400, 'Missing id parameter')
                return

            supabase = get_supabase()
            response = supabase.table('prospects').select('*').eq('id', prospect_id).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data[0] if response.data else None).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_PUT(self):
        try:
            prospect_id = self.get_id()
            if not prospect_id:
                self.send_error(400, 'Missing id parameter')
                return

            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())

            validation_error = validate_payload(data)
            if validation_error:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': validation_error}).encode())
                return

            supabase = get_supabase()
            response = supabase.table('prospects').update({
                'name': data.get('name'),
                'company': data.get('company'),
                'email': data.get('email'),
                'linkedin': data.get('linkedin'),
                'notes': data.get('notes'),
                'status': data.get('status'),
                'next_followup': data.get('next_followup'),
                'next_action': data.get('next_action'),
                'next_action_due_date': data.get('next_action_due_date'),
                'action_channel': data.get('action_channel'),
                'action_objective': data.get('action_objective')
            }).eq('id', prospect_id).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data[0] if response.data else None).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_DELETE(self):
        try:
            prospect_id = self.get_id()
            if not prospect_id:
                self.send_error(400, 'Missing id parameter')
                return

            supabase = get_supabase()
            supabase.table('prospects').delete().eq('id', prospect_id).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_PATCH(self):
        try:
            prospect_id = self.get_id()
            if not prospect_id:
                self.send_error(400, 'Missing id parameter')
                return

            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())

            supabase = get_supabase()
            current = supabase.table('prospects').select('*').eq('id', prospect_id).execute()
            current_row = current.data[0] if current.data else None
            if not current_row:
                self.send_error(404, 'Prospect not found')
                return

            merged = {**current_row, **data}
            validation_error = validate_payload(merged)
            if validation_error:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': validation_error}).encode())
                return

            response = supabase.table('prospects').update({
                'status': data.get('status')
            }).eq('id', prospect_id).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data[0] if response.data else None).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
