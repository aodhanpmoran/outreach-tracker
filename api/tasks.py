from http.server import BaseHTTPRequestHandler
import json
import os
from urllib.parse import urlparse, parse_qs
from supabase import create_client

from _auth import require_api_key

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """List all tasks, optionally filtered by date or completion status"""
        if not require_api_key(self):
            return

        try:
            query = parse_qs(urlparse(self.path).query)
            date_entered = query.get('date_entered', [None])[0]
            completed = query.get('completed', [None])[0]
            limit_param = query.get('limit', [None])[0]
            offset_param = query.get('offset', [None])[0]

            supabase = get_supabase()
            q = supabase.table('tasks').select('*')

            if date_entered:
                q = q.eq('date_entered', date_entered)
            if completed is not None:
                q = q.eq('completed', completed.lower() == 'true')

            q = q.order('date_entered', desc=True).order('created_at', desc=True)

            if limit_param:
                try:
                    limit = max(1, min(int(limit_param), 1000))
                except ValueError:
                    limit = 200
                try:
                    offset = max(0, int(offset_param or 0))
                except ValueError:
                    offset = 0
                q = q.range(offset, offset + limit - 1)

            response = q.execute()

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
        """Create a new task"""
        if not require_api_key(self):
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())

            supabase = get_supabase()
            response = supabase.table('tasks').insert({
                'text': data.get('text'),
                'completed': data.get('completed', False),
                'date_entered': data.get('date_entered'),
                'date_scheduled': data.get('date_scheduled')
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
