import json
import os

def _send_json(handler, status_code: int, payload: dict):
    handler.send_response(status_code)
    handler.send_header('Content-type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json.dumps(payload).encode())


def require_api_key(handler) -> bool:
    """Return True when request is authorized.

    If `OUTREACH_TRACKER_API_KEY` is unset, auth is disabled.
    """

    expected = os.environ.get('OUTREACH_TRACKER_API_KEY')
    if not expected:
        return True

    provided = handler.headers.get('X-Api-Key')
    if provided and provided == expected:
        return True

    _send_json(handler, 401, {'error': 'Unauthorized'})
    return False
