from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime, timedelta
from supabase import create_client
import resend
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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

def build_email_html(stats, planning):
    status_labels = {
        'new': 'Target',
        'contacted': 'Contacted',
        'responded': 'Present',
        'call_scheduled': 'Propose',
        'closed': 'Close',
        'pilot': 'Delivery',
        'client': 'Report',
        'lost': 'Recontact'
    }

    status_rows = ""
    for status, label in status_labels.items():
        count = stats['by_status'].get(status, 0)
        status_rows += f"<tr><td style='padding: 8px; border-bottom: 1px solid #eee;'>{label}</td><td style='padding: 8px; border-bottom: 1px solid #eee; text-align: right; font-weight: bold;'>{count}</td></tr>"

    # Build yesterday's completed tasks section
    yesterday_section = ""
    if planning.get('yesterday'):
        yesterday_data = planning['yesterday']
        yesterday_tasks = json.loads(yesterday_data.get('tasks', '[]'))
        completed_tasks = [t for t in yesterday_tasks if t.get('completed')]

        if completed_tasks:
            yesterday_section = """
            <div style="background: #f0fdf4; border-left: 4px solid #22c55e; padding: 16px; border-radius: 8px; margin-bottom: 24px;">
                <h3 style="margin: 0 0 12px 0; color: #166534; font-size: 16px;">✅ Completed Yesterday</h3>
            """
            for task in completed_tasks:
                if task.get('text'):
                    yesterday_section += f"<div style='padding: 6px 0; color: #166534;'>✓ {task['text']}</div>"
            yesterday_section += "</div>"

    # Build today's tasks section
    today_section = ""
    if planning.get('today'):
        today_data = planning['today']
        today_one_thing = today_data.get('one_thing', '')
        today_tasks = json.loads(today_data.get('tasks', '[]'))

        if today_one_thing or today_tasks:
            today_section = """
            <div style="background: #eff6ff; border-left: 4px solid #2563eb; padding: 16px; border-radius: 8px; margin-bottom: 24px;">
                <h3 style="margin: 0 0 12px 0; color: #1e40af; font-size: 16px;">🎯 Today's Focus</h3>
            """
            if today_one_thing:
                today_section += f"<div style='font-weight: bold; color: #1e40af; margin-bottom: 12px; font-size: 15px;'>The One Thing: {today_one_thing}</div>"
            if today_tasks:
                today_section += "<div style='color: #1e40af;'>Main Tasks:</div>"
                for task in today_tasks:
                    if task.get('text'):
                        today_section += f"<div style='padding: 6px 0 6px 20px; color: #1e40af;'>• {task['text']}</div>"
            today_section += "</div>"

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">
        <h1 style="color: #2563eb; margin-bottom: 8px;">Daily Outreach Summary</h1>
        <p style="color: #64748b; margin-bottom: 24px;">Your pipeline at a glance</p>

        {yesterday_section}
        {today_section}

        <div style="background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%); color: white; padding: 24px; border-radius: 12px; margin-bottom: 24px;">
            <div style="font-size: 14px; opacity: 0.9;">Total Prospects</div>
            <div style="font-size: 48px; font-weight: bold;">{stats['total']}</div>
            <div style="font-size: 14px; margin-top: 8px;">Conversion Rate: {stats['conversion_rate']}%</div>
        </div>

        <table style="width: 100%; border-collapse: collapse; background: #f8fafc; border-radius: 8px; overflow: hidden;">
            <thead>
                <tr style="background: #e2e8f0;">
                    <th style="padding: 12px 8px; text-align: left;">Status</th>
                    <th style="padding: 12px 8px; text-align: right;">Count</th>
                </tr>
            </thead>
            <tbody>
                {status_rows}
            </tbody>
        </table>

        <p style="color: #64748b; font-size: 12px; margin-top: 24px; text-align: center;">
            Keep pushing toward your goal!<br>
            <strong style="color: #2563eb;">€5k → €10k for Paula & the kids</strong>
        </p>
    </div>
    """

def build_telegram_message(stats, planning):
    status_labels = {
        'new': 'Target',
        'contacted': 'Contacted',
        'responded': 'Present',
        'call_scheduled': 'Propose',
        'closed': 'Close',
        'pilot': 'Delivery',
        'client': 'Report',
        'lost': 'Recontact'
    }

    lines = [
        "Daily Outreach Summary",
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
            lines.append("Completed yesterday:")
            lines.extend([f"- {t['text']}" for t in completed_tasks])

    today = planning.get('today') or {}
    today_one_thing = today.get('one_thing') or ""
    if today_one_thing or today.get('tasks'):
        lines.append("")
        lines.append("Today's focus:")
        if today_one_thing:
            lines.append(f"- One thing: {today_one_thing}")
        today_tasks = json.loads(today.get('tasks') or '[]')
        for task in today_tasks:
            if task.get('text'):
                lines.append(f"- {task['text']}")

    lines.extend([
        "",
        "Goal: EUR 5k -> EUR 10k for Paula & the kids"
    ])

    return "\n".join(lines)

def send_telegram_message(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return {
            'success': False,
            'skipped': True,
            'error': 'Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID'
        }

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
        body = response.read().decode('utf-8')

    return json.loads(body)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
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

            # Get stats and planning from Supabase
            supabase = get_supabase()
            stats = get_stats(supabase)
            planning = get_daily_planning(supabase)

            # Send email via Resend
            resend.api_key = os.environ.get('RESEND_API_KEY')

            email_response = resend.Emails.send({
                "from": "Homebase <onboarding@resend.dev>",
                "to": os.environ.get('SUMMARY_EMAIL', 'aodhanpmoran@gmail.com'),
                "subject": f"Daily Outreach Summary - {stats['total']} prospects",
                "html": build_email_html(stats, planning)
            })

            telegram_result = {}
            try:
                telegram_message = build_telegram_message(stats, planning)
                telegram_result = send_telegram_message(telegram_message)
            except Exception as telegram_error:
                telegram_result = {
                    'success': False,
                    'error': str(telegram_error)
                }

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'email_id': email_response.get('id') if isinstance(email_response, dict) else str(email_response),
                'telegram': telegram_result,
                'stats': stats
            }).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
