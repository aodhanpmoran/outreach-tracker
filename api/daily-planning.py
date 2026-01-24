from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from supabase import create_client

from _auth import require_api_key

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

def normalize_tasks(raw_tasks):
    """Normalize tasks from various formats into consistent structure."""
    if raw_tasks is None:
        return []

    if isinstance(raw_tasks, str):
        try:
            raw_tasks = json.loads(raw_tasks) if raw_tasks else []
        except json.JSONDecodeError:
            return []

    if not isinstance(raw_tasks, list):
        return []

    normalized = []
    for item in raw_tasks:
        if isinstance(item, dict):
            normalized.append({
                'text': item.get('text', ''),
                'completed': bool(item.get('completed', False)),
                'dbId': item.get('dbId') or item.get('db_id')
            })
        elif isinstance(item, str):
            normalized.append({
                'text': item,
                'completed': False,
                'dbId': None
            })
    return normalized

def find_existing_task_id(supabase, date_str, text):
    """Find existing task in history by date and text."""
    if not text:
        return None
    response = supabase.table('tasks').select('id').eq('date_entered', date_str).eq('text', text).limit(1).execute()
    if response.data:
        return response.data[0].get('id')
    return None

def ensure_task_history(supabase, tasks, date_str):
    """Ensure all tasks have entries in the tasks table for history tracking."""
    updated = []
    for task in tasks:
        if task.get('text') and not task.get('dbId'):
            existing_id = find_existing_task_id(supabase, date_str, task['text'])
            if existing_id:
                task['dbId'] = existing_id
            else:
                response = supabase.table('tasks').insert({
                    'text': task['text'],
                    'completed': task.get('completed', False),
                    'date_entered': date_str
                }).execute()
                if response.data:
                    task['dbId'] = response.data[0].get('id')
        updated.append(task)
    return updated

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Fetch daily planning by date (defaults to today)"""
        if not require_api_key(self):
            return

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
        if not require_api_key(self):
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())

            supabase = get_supabase()

            # Upsert daily planning for specified date (or today if not specified)
            planning_date = data.get('date', datetime.now().date().isoformat())

            # Normalize and ensure task history for all tasks with text
            raw_tasks = data.get('tasks', [])
            tasks = normalize_tasks(raw_tasks)
            tasks = ensure_task_history(supabase, tasks, planning_date)

            planning_data = {
                'date': planning_date,
                'one_thing': data.get('oneThing', ''),
                'tasks': json.dumps(tasks),
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
            self.wfile.write(json.dumps({'success': True, 'tasks': tasks}).encode())

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
