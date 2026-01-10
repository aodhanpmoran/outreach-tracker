from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from supabase import create_client

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Fetch daily planning by date (defaults to today)"""
        try:
            query = parse_qs(urlparse(self.path).query)
            date_param = query.get('date', [None])[0]
            planning_date = date_param or datetime.now().date().isoformat()

            supabase = get_supabase()
            response = supabase.table('daily_planning').select('*').eq('date', planning_date).execute()
            planning = response.data[0] if response.data else None

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(planning).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_POST(self):
        """Save daily planning (one thing + tasks)"""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())

            supabase = get_supabase()

            # Upsert daily planning for specified date (or today if not specified)
            planning_date = data.get('date', datetime.now().date().isoformat())
            planning_data = {
                'date': planning_date,
                'one_thing': data.get('oneThing', ''),
                'tasks': json.dumps(data.get('tasks', [])),
                'updated_at': datetime.now().isoformat()
            }

            response = supabase.table('daily_planning').upsert(
                planning_data,
                on_conflict='date'
            ).execute()

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

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
