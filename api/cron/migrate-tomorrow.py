from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime, timedelta
from supabase import create_client

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Migrate yesterday's tomorrow planning to today at midnight"""
        try:
            # Verify cron secret (optional security)
            auth_header = self.headers.get('Authorization')
            cron_secret = os.environ.get('CRON_SECRET')

            if cron_secret and auth_header != f'Bearer {cron_secret}':
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode())
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
                tasks = json.loads(today_data.get('tasks', '[]'))
                # Reset all tasks to uncompleted
                for task in tasks:
                    task['completed'] = False

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
