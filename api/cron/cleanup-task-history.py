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

def normalize_text(text):
    return " ".join((text or "").split()).strip()

def build_key(date_str, text):
    if not date_str or not text:
        return None
    return f"{date_str}|{normalize_text(text)}"

def pick_canonical(rows):
    def score(row):
        completed = 1 if row.get('completed') else 0
        completed_at = row.get('completed_at') or ''
        updated_at = row.get('updated_at') or ''
        created_at = row.get('created_at') or ''
        return (completed, completed_at, updated_at, created_at, row.get('id') or 0)

    return max(rows, key=score)

def normalize_planning_tasks(raw_tasks):
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
            task = dict(item)
            if 'dbId' not in task and 'db_id' in task:
                task['dbId'] = task.get('db_id')
            normalized.append(task)
        elif isinstance(item, str):
            normalized.append({
                'text': item,
                'completed': False,
                'dbId': None
            })
    return normalized

def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Deduplicate tasks history and re-link daily_planning dbIds."""
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
            dry_run = query.get('dry_run', ['0'])[0] in ('1', 'true', 'yes')

            supabase = get_supabase()
            tasks_response = supabase.table('tasks').select('*').execute()
            tasks = tasks_response.data or []

            groups = {}
            for task in tasks:
                date_str = task.get('date_entered') or ''
                text = task.get('text') or ''
                key = build_key(date_str, text)
                if key:
                    groups.setdefault(key, []).append(task)

            duplicate_map = {}
            duplicate_ids = []
            canonical_by_key = {}
            affected_dates = set()
            duplicate_groups = 0

            for key, rows in groups.items():
                canonical = pick_canonical(rows)
                canonical_id = canonical.get('id')
                canonical_by_key[key] = canonical_id
                if len(rows) <= 1:
                    continue
                duplicate_groups += 1
                affected_dates.add(key.split('|', 1)[0])
                for row in rows:
                    row_id = row.get('id')
                    if row_id and row_id != canonical_id:
                        duplicate_map[row_id] = canonical_id
                        duplicate_ids.append(row_id)

            rows_updated = 0
            if duplicate_map:
                for date_str in affected_dates:
                    planning_response = supabase.table('daily_planning').select('*').eq('date', date_str).execute()
                    if not planning_response.data:
                        continue

                    row = planning_response.data[0]
                    tasks_list = normalize_planning_tasks(row.get('tasks'))
                    changed = False

                    for task in tasks_list:
                        text = task.get('text', '')
                        key = build_key(date_str, text)
                        canonical_id = canonical_by_key.get(key) if key else None

                        existing_id = task.get('dbId')
                        if existing_id in duplicate_map:
                            task['dbId'] = duplicate_map[existing_id]
                            if 'db_id' in task:
                                task.pop('db_id', None)
                            changed = True
                        elif canonical_id and existing_id != canonical_id:
                            task['dbId'] = canonical_id
                            if 'db_id' in task:
                                task.pop('db_id', None)
                            changed = True

                    if changed and not dry_run:
                        supabase.table('daily_planning').update({
                            'tasks': json.dumps(tasks_list),
                            'updated_at': datetime.now(timezone.utc).isoformat()
                        }).eq('date', date_str).execute()
                        rows_updated += 1

            deleted = 0
            if duplicate_ids and not dry_run:
                for chunk in chunked(duplicate_ids, 100):
                    supabase.table('tasks').delete().in_('id', chunk).execute()
                    deleted += len(chunk)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'dry_run': dry_run,
                'total_tasks': len(tasks),
                'duplicate_groups': duplicate_groups,
                'duplicates_found': len(duplicate_ids),
                'duplicates_deleted': deleted,
                'planning_rows_updated': rows_updated
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
