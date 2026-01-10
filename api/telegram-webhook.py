from http.server import BaseHTTPRequestHandler
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from supabase import create_client

MAX_TASKS = 3

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

def get_stats(supabase):
    prospects = supabase.table('prospects').select('*').execute()
    all_prospects = prospects.data

    status_counts = {}
    for p in all_prospects:
        status = p.get('status', 'new')
        status_counts[status] = status_counts.get(status, 0) + 1

    total = len(all_prospects)
    closed = status_counts.get('closed', 0)

    return {
        'total': total,
        'by_status': status_counts,
        'conversion_rate': round((closed / total * 100), 1) if total > 0 else 0
    }

def get_daily_planning(supabase):
    today = datetime.now().date().isoformat()
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()

    # Get yesterday's planning
    yesterday_response = supabase.table('daily_planning').select('*').eq('date', yesterday).execute()
    yesterday_data = yesterday_response.data[0] if yesterday_response.data else None

    # Get today's planning
    today_response = supabase.table('daily_planning').select('*').eq('date', today).execute()
    today_data = today_response.data[0] if today_response.data else None

    return {
        'yesterday': yesterday_data,
        'today': today_data
    }

def build_daily_update_message(stats, planning):
    status_labels = {
        'new': 'New',
        'contacted': 'Contacted',
        'responded': 'Responded',
        'call_scheduled': 'Call Scheduled',
        'closed': 'Closed',
        'lost': 'Lost'
    }

    lines = [
        "📊 Daily Outreach Update",
        "",
        f"Total prospects: {stats['total']}",
        f"Conversion rate: {stats['conversion_rate']}%",
        "",
        "Pipeline:"
    ]

    for status, label in status_labels.items():
        count = stats['by_status'].get(status, 0)
        lines.append(f"- {label}: {count}")

    yesterday = planning.get('yesterday') or {}
    if yesterday.get('tasks'):
        yesterday_tasks = json.loads(yesterday.get('tasks') or '[]')
        completed_tasks = [t for t in yesterday_tasks if t.get('completed') and t.get('text')]
        if completed_tasks:
            lines.append("")
            lines.append("✅ Completed yesterday:")
            lines.extend([f"- {t['text']}" for t in completed_tasks])

    today = planning.get('today') or {}
    today_one_thing = today.get('one_thing') or ""
    if today_one_thing or today.get('tasks'):
        lines.append("")
        lines.append("🎯 Today's focus:")
        if today_one_thing:
            lines.append(f"- One thing: {today_one_thing}")
        today_tasks = json.loads(today.get('tasks') or '[]')
        for task in today_tasks:
            if task.get('text'):
                status_icon = "✓" if task.get('completed') else "•"
                lines.append(f"{status_icon} {task['text']}")

    lines.extend([
        "",
        "💰 Goal: EUR 5k → EUR 10k for Paula & the kids"
    ])

    return "\n".join(lines)

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

def ensure_task_slot(tasks, index):
    while len(tasks) <= index:
        tasks.append({
            'text': '',
            'completed': False,
            'dbId': None
        })
    return tasks

def parse_updates(text):
    updates = {
        'today': {'one_thing': None, 'tasks': {}},
        'tomorrow': {'one_thing': None, 'tasks': {}}
    }
    ignored = []

    if not text:
        return updates, ignored

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        target = 'today'
        lower = line.lower()
        if lower.startswith('tomorrow '):
            target = 'tomorrow'
            line = line[len('tomorrow '):].strip()
        elif lower.startswith('tomorrow:'):
            target = 'tomorrow'
            line = line[len('tomorrow:'):].strip()
        elif lower.startswith('today '):
            line = line[len('today '):].strip()
        elif lower.startswith('today:'):
            line = line[len('today:'):].strip()

        if not line:
            continue

        one_match = re.match(r"^(one\s*thing|one)\s*[:\-]\s*(.+)$", line, re.IGNORECASE)
        if one_match:
            value = one_match.group(2).strip()
            if value:
                updates[target]['one_thing'] = value
            else:
                ignored.append(raw_line)
            continue

        task_match = re.match(r"^task\s*([1-3])\s*[:\-]\s*(.+)$", line, re.IGNORECASE)
        if task_match:
            index = int(task_match.group(1)) - 1
            value = task_match.group(2).strip()
            if value:
                updates[target]['tasks'][index] = value
            else:
                ignored.append(raw_line)
            continue

        ignored.append(raw_line)

    return updates, ignored

def send_telegram_message(chat_id, message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        return None

    payload = urlencode({
        'chat_id': chat_id,
        'text': message,
        'disable_web_page_preview': 'true'
    }).encode('utf-8')

    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )

    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode('utf-8'))

def find_existing_task(supabase, date_str, text):
    if not text:
        return None
    response = supabase.table('tasks').select('id').eq('date_entered', date_str).eq('text', text).limit(1).execute()
    if response.data:
        return response.data[0].get('id')
    return None

def update_task_history(supabase, task, text, date_str):
    old_text = task.get('text') or ''
    task['text'] = text
    task['completed'] = False
    task_id = task.get('dbId')
    now = datetime.now(timezone.utc).isoformat()

    if task_id:
        supabase.table('tasks').update({
            'text': text,
            'completed': False,
            'completed_at': None,
            'updated_at': now
        }).eq('id', task_id).execute()
        return task

    if old_text:
        existing_id = find_existing_task(supabase, date_str, old_text)
        if existing_id:
            task_id = existing_id

    if not task_id:
        existing_id = find_existing_task(supabase, date_str, text)
        if existing_id:
            task_id = existing_id

    if task_id:
        supabase.table('tasks').update({
            'text': text,
            'completed': False,
            'completed_at': None,
            'updated_at': now
        }).eq('id', task_id).execute()
        task['dbId'] = task_id
        return task

    response = supabase.table('tasks').insert({
        'text': text,
        'completed': False,
        'date_entered': date_str
    }).execute()

    if response.data:
        task['dbId'] = response.data[0].get('id')
    return task

def apply_updates(supabase, date_str, updates, update_history):
    if updates['one_thing'] is None and not updates['tasks']:
        return None

    existing = supabase.table('daily_planning').select('*').eq('date', date_str).execute()
    row = existing.data[0] if existing.data else {}

    tasks = normalize_tasks(row.get('tasks'))
    tasks = tasks[:MAX_TASKS]
    one_thing = row.get('one_thing') or ''

    if updates['one_thing'] is not None:
        one_thing = updates['one_thing']

    for index, text in updates['tasks'].items():
        if index >= MAX_TASKS:
            continue
        tasks = ensure_task_slot(tasks, index)
        if update_history:
            tasks[index] = update_task_history(supabase, tasks[index], text, date_str)
        else:
            tasks[index]['text'] = text
            tasks[index]['completed'] = False

    supabase.table('daily_planning').upsert({
        'date': date_str,
        'one_thing': one_thing,
        'tasks': json.dumps(tasks),
        'updated_at': datetime.now(timezone.utc).isoformat()
    }, on_conflict='date').execute()

    return {
        'one_thing': one_thing,
        'tasks': tasks
    }

def build_confirmation(today_result, tomorrow_result, ignored_lines):
    lines = []

    if today_result:
        lines.append("Updated today:")
        if today_result.get('one_thing'):
            lines.append(f"- One thing: {today_result['one_thing']}")
        for i, task in enumerate(today_result.get('tasks', []), start=1):
            if task.get('text'):
                lines.append(f"- Task {i}: {task['text']}")

    if tomorrow_result:
        if lines:
            lines.append("")
        lines.append("Updated tomorrow:")
        if tomorrow_result.get('one_thing'):
            lines.append(f"- One thing: {tomorrow_result['one_thing']}")
        for i, task in enumerate(tomorrow_result.get('tasks', []), start=1):
            if task.get('text'):
                lines.append(f"- Task {i}: {task['text']}")

    if ignored_lines:
        if lines:
            lines.append("")
        lines.append("Ignored lines:")
        lines.extend([f"- {line}" for line in ignored_lines[:5]])
        if len(ignored_lines) > 5:
            lines.append("- ...")

    if not lines:
        lines = [
            "No updates found.",
            "Use:",
            "one: Focus item",
            "task1: First task",
            "task2: Second task",
            "tomorrow task1: Future task"
        ]

    return "\n".join(lines)

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            secret = os.environ.get('TELEGRAM_WEBHOOK_SECRET')
            if secret:
                header_secret = self.headers.get('X-Telegram-Bot-Api-Secret-Token')
                if header_secret != secret:
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode())
                    return

            content_length = int(self.headers.get('Content-Length', 0))
            payload = self.rfile.read(content_length)
            update = json.loads(payload.decode('utf-8') or '{}')

            message = update.get('message') or update.get('edited_message') or {}
            text = message.get('text', '')
            chat_id = message.get('chat', {}).get('id')

            allowed_chat = os.environ.get('TELEGRAM_CHAT_ID')
            if allowed_chat and str(chat_id) != str(allowed_chat):
                self.send_response(403)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Forbidden'}).encode())
                return

            # Check if this is an "update" command
            if text.strip().lower() in ['update', '/update']:
                supabase = get_supabase()
                stats = get_stats(supabase)
                planning = get_daily_planning(supabase)
                daily_update_message = build_daily_update_message(stats, planning)

                if chat_id:
                    send_telegram_message(chat_id, daily_update_message)

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'command': 'update',
                    'stats': stats
                }).encode())
                return

            updates, ignored = parse_updates(text)

            supabase = get_supabase()
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)

            today_result = apply_updates(
                supabase,
                today.isoformat(),
                updates['today'],
                update_history=True
            )
            tomorrow_result = apply_updates(
                supabase,
                tomorrow.isoformat(),
                updates['tomorrow'],
                update_history=False
            )

            confirmation = build_confirmation(today_result, tomorrow_result, ignored)
            if chat_id and confirmation:
                send_telegram_message(chat_id, confirmation)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'today': today_result,
                'tomorrow': tomorrow_result,
                'ignored': ignored
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
