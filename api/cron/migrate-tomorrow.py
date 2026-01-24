from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime, timedelta
from supabase import create_client

from _auth import require_api_key

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

def normalize_tasks(raw_tasks):
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
    if not text:
        return None
    response = supabase.table('tasks').select('id').eq('date_entered', date_str).eq('text', text).limit(1).execute()
    if response.data:
        return response.data[0].get('id')
    return None

def ensure_task_history(supabase, tasks, date_str):
    updated = []
    for task in tasks:
        task['completed'] = False
        if task.get('text') and not task.get('dbId'):
            existing_id = find_existing_task_id(supabase, date_str, task['text'])
            if existing_id:
                task['dbId'] = existing_id
            else:
                response = supabase.table('tasks').insert({
                    'text': task['text'],
                    'completed': False,
                    'date_entered': date_str
                }).execute()
                if response.data:
                    task['dbId'] = response.data[0].get('id')
        updated.append(task)
    return updated

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Migrate yesterday's tomorrow planning to today at midnight"""
        try:
            # Authorize via cron secret OR API key
            auth_header = self.headers.get('Authorization')
            cron_secret = os.environ.get('CRON_SECRET')

            if cron_secret and auth_header == f'Bearer {cron_secret}':
                pass
            else:
                if not require_api_key(self):
                    return

            supabase = get_supabase()

            today = datetime.now().date().isoformat()
            yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()

            # Get yesterday's planning (contains yesterday's actual tasks)
            yesterday_response = supabase.table('daily_planning').select('*').eq('date', yesterday).execute()
            yesterday_data = yesterday_response.data[0] if yesterday_response.data else None

            # Get today's planning (was planned as "tomorrow" yesterday)
            today_response = supabase.table('daily_planning').select('*').eq('date', today).execute()
            today_data = today_response.data[0] if today_response.data else None

            # If today already has planning, reset completion status
            if today_data:
                tasks = normalize_tasks(today_data.get('tasks'))
                tasks = ensure_task_history(supabase, tasks, today)

                # Update today's planning with reset tasks
                supabase.table('daily_planning').update({
                    'tasks': json.dumps(tasks),
                    'updated_at': datetime.now().isoformat()
                }).eq('date', today).execute()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'message': 'Tomorrow migrated to today',
                'today_date': today
            }).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
