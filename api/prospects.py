from http.server import BaseHTTPRequestHandler
import json
import os
from supabase import create_client
from urllib.parse import urlparse, parse_qs

from _cors import set_cors

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
            set_cors(self)
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

            supabase = get_supabase()
            response = supabase.table('prospects').insert({
                'name': data.get('name'),
                'company': data.get('company'),
                'email': data.get('email'),
                'linkedin': data.get('linkedin'),
                'notes': data.get('notes'),
                'status': data.get('status', 'new'),
                'next_followup': data.get('next_followup')
            }).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            set_cors(self)
            self.end_headers()
            self.wfile.write(json.dumps(response.data[0]).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        set_cors(self)
        self.end_headers()
