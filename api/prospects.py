from http.server import BaseHTTPRequestHandler
import json
import os
from supabase import create_client
from urllib.parse import urlparse, parse_qs

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
    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            fields = query.get('fields', [None])[0]

            supabase = get_supabase()
            select_fields = fields if fields else '*'
            response = supabase.table('prospects').select(select_fields).order('updated_at', desc=True).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_POST(self):
        try:
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
            response = supabase.table('prospects').insert({
                'name': data.get('name'),
                'company': data.get('company'),
                'email': data.get('email'),
                'linkedin': data.get('linkedin'),
                'notes': data.get('notes'),
                'status': data.get('status', 'new'),
                'next_followup': data.get('next_followup'),
                'next_action': data.get('next_action'),
                'next_action_due_date': data.get('next_action_due_date'),
                'action_channel': data.get('action_channel'),
                'action_objective': data.get('action_objective')
            }).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data[0]).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
