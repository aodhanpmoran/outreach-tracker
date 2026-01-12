from http.server import BaseHTTPRequestHandler
import json
import os
from urllib.parse import urlparse, parse_qs
from supabase import create_client

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

class handler(BaseHTTPRequestHandler):
    def get_id(self):
        query = parse_qs(urlparse(self.path).query)
        return query.get('id', [None])[0]

    def do_GET(self):
        """Get a single Fathom call with action items"""
        try:
            call_id = self.get_id()
            if not call_id:
                self.send_error(400, 'Missing id parameter')
                return

            supabase = get_supabase()
            response = supabase.table('fathom_calls').select(
                '*, fathom_action_items(id, description, assignee, completed, completed_at, task_id), prospects(id, name, company)'
            ).eq('id', call_id).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data[0] if response.data else None).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_PATCH(self):
        """Update a Fathom call (mainly for manual linking to prospect)"""
        try:
            call_id = self.get_id()
            if not call_id:
                self.send_error(400, 'Missing id parameter')
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
                self.send_error(400, 'No valid fields to update')
                return

            response = supabase.table('fathom_calls').update(update_data).eq('id', call_id).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response.data[0] if response.data else None).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_DELETE(self):
        """Delete a Fathom call (cascade deletes action items)"""
        try:
            call_id = self.get_id()
            if not call_id:
                self.send_error(400, 'Missing id parameter')
                return

            supabase = get_supabase()
            supabase.table('fathom_calls').delete().eq('id', call_id).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, PATCH, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
