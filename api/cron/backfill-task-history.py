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
    inserted = 0
    linked = 0
    now = datetime.now(timezone.utc).isoformat()

    for task in tasks:
        text = task.get('text', '').strip()
        if not text:
            updated.append(task)
            continue

        task_id = task.get('dbId')
        if not task_id:
            existing_id = find_existing_task_id(supabase, date_str, text)
            if existing_id:
                task['dbId'] = existing_id
                linked += 1
            else:
                payload = {
                    'text': text,
                    'completed': bool(task.get('completed', False)),
                    'date_entered': date_str
                }
                if payload['completed']:
                    payload['completed_at'] = now
                response = supabase.table('tasks').insert(payload).execute()
                if response.data:
                    task['dbId'] = response.data[0].get('id')
                    inserted += 1

        updated.append(task)

    return updated, inserted, linked

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Backfill missing tasks into history from daily_planning."""
        try:
            auth_header = self.headers.get('Authorization')
            cron_secret = os.environ.get('CRON_SECRET')

            if cron_secret and auth_header != f'Bearer {cron_secret}':
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode())
                return

            query = parse_qs(urlparse(self.path).query)
            from_date = query.get('from', [None])[0]
            to_date = query.get('to', [None])[0]

            supabase = get_supabase()
            planning_query = supabase.table('daily_planning').select('*')
            if from_date:
                planning_query = planning_query.gte('date', from_date)
            if to_date:
                planning_query = planning_query.lte('date', to_date)

            response = planning_query.order('date', desc=False).execute()
            rows = response.data or []

            totals = {
                'planning_rows': 0,
                'tasks_checked': 0,
                'tasks_inserted': 0,
                'tasks_linked': 0,
                'rows_updated': 0
            }

            for row in rows:
                date_str = row.get('date')
                tasks = normalize_tasks(row.get('tasks'))
                totals['planning_rows'] += 1
                totals['tasks_checked'] += len(tasks)

                updated_tasks, inserted, linked = ensure_task_history(supabase, tasks, date_str)
                totals['tasks_inserted'] += inserted
                totals['tasks_linked'] += linked

                if json.dumps(tasks) != json.dumps(updated_tasks):
                    supabase.table('daily_planning').update({
                        'tasks': json.dumps(updated_tasks),
                        'updated_at': datetime.now(timezone.utc).isoformat()
                    }).eq('date', date_str).execute()
                    totals['rows_updated'] += 1

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'totals': totals,
                'from': from_date,
                'to': to_date
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
