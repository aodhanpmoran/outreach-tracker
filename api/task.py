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
        """Get a single task by ID"""
        try:
            task_id = self.get_id()
            if not task_id:
                self.send_error(400, 'Missing id parameter')
                return

            supabase = get_supabase()
            response = supabase.table('tasks').select('*').eq('id', task_id).execute()

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
        """Update a task (full update)"""
        try:
            task_id = self.get_id()
            if not task_id:
                self.send_error(400, 'Missing id parameter')
                return

            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())

            update_data = {
                'text': data.get('text'),
                'completed': data.get('completed'),
                'date_scheduled': data.get('date_scheduled'),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }

            # Set completed_at if marking as complete
            if data.get('completed'):
                update_data['completed_at'] = datetime.now(timezone.utc).isoformat()
            else:
                update_data['completed_at'] = None

            supabase = get_supabase()
            response = supabase.table('tasks').update(update_data).eq('id', task_id).execute()

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

    def do_PATCH(self):
        """Toggle task completion status"""
        try:
            task_id = self.get_id()
            if not task_id:
                self.send_error(400, 'Missing id parameter')
                return

            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())

            update_data = {
                'completed': data.get('completed'),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }

            # Set completed_at timestamp when marking complete
            if data.get('completed'):
                update_data['completed_at'] = datetime.now(timezone.utc).isoformat()
            else:
                update_data['completed_at'] = None

            supabase = get_supabase()
            response = supabase.table('tasks').update(update_data).eq('id', task_id).execute()

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
        """Delete a task"""
        try:
            task_id = self.get_id()
            if not task_id:
                self.send_error(400, 'Missing id parameter')
                return

            supabase = get_supabase()
            supabase.table('tasks').delete().eq('id', task_id).execute()

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
        self.send_header('Access-Control-Allow-Methods', 'GET, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
