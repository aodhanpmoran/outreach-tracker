from http.server import BaseHTTPRequestHandler
import json
import os
from supabase import create_client

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return create_client(url, key)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            supabase = get_supabase()

            # Get all prospects
            response = supabase.table('prospects').select('*').execute()
            prospects = response.data

            # Calculate stats
            total = len(prospects)
            status_counts = {}
            for p in prospects:
                status = p.get('status', 'new')
                status_counts[status] = status_counts.get(status, 0) + 1

            # Calculate rates
            closed = status_counts.get('closed', 0)
            conversion_rate = round((closed / total * 100), 1) if total > 0 else 0

            responded_plus = (
                status_counts.get('responded', 0) +
                status_counts.get('call_scheduled', 0) +
                status_counts.get('closed', 0) +
                status_counts.get('lost', 0)
            )
            contacted_total = total - status_counts.get('new', 0)
            response_rate = round((responded_plus / max(contacted_total, 1) * 100), 1) if contacted_total > 0 else 0

            result = {
                'total': total,
                'by_status': status_counts,
                'week_added': 0,  # Simplified for now
                'week_contacted': 0,
                'conversion_rate': conversion_rate,
                'response_rate': response_rate
            }

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
