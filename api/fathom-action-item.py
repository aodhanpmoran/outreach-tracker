from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime, timezone
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
        """Get action items, optionally filtered by call_id"""
        try:
            query = parse_qs(urlparse(self.path).query)
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
                # Get all incomplete action items
                response = supabase.table('fathom_action_items').select(
                    '*, fathom_calls(id, title, call_date, prospect_id)'
                ).eq('completed', False).order('created_at', desc=True).limit(50).execute()
                result = response.data

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_PATCH(self):
        """Update an action item (mark complete, link to task)"""
        try:
            action_id = self.get_id()
            if not action_id:
                self.send_error(400, 'Missing id parameter')
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
                self.send_error(400, 'No valid fields to update')
                return

            response = supabase.table('fathom_action_items').update(update_data).eq('id', action_id).execute()

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

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
