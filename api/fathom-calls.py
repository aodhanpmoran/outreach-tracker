from http.server import BaseHTTPRequestHandler
import json
import os
from supabase import create_client
from urllib.parse import urlparse, parse_qs

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Get list of Fathom calls with optional filtering"""
        try:
            query = parse_qs(urlparse(self.path).query)
            prospect_id = query.get('prospect_id', [None])[0]
            unmatched = query.get('unmatched', ['false'])[0].lower() == 'true'
            needs_review = query.get('needs_review', ['false'])[0].lower() == 'true'
            limit = int(query.get('limit', ['50'])[0])
            offset = int(query.get('offset', ['0'])[0])

            supabase = get_supabase()

            # Build query
            q = supabase.table('fathom_calls').select('*, fathom_action_items(id, description, completed, task_id)')

            if prospect_id:
                q = q.eq('prospect_id', prospect_id)
            elif unmatched:
                q = q.is_('prospect_id', 'null')

            if needs_review:
                q = q.eq('needs_review', True)

            response = q.order('call_date', desc=True).range(offset, offset + limit - 1).execute()

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
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
