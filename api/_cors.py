import os

def _parse_allowlist():
    raw = os.environ.get('CORS_ALLOW_ORIGINS', '')
    return {origin.strip() for origin in raw.split(',') if origin.strip()}


def set_cors(handler):
    """Set CORS headers if request Origin is allowlisted.

    Configure allowlist via `CORS_ALLOW_ORIGINS` (comma-separated).
    If unset/empty, no Origin is allowed.
    """

    origin = handler.headers.get('Origin')
    allowlist = _parse_allowlist()

    if origin and origin in allowlist:
        handler.send_header('Access-Control-Allow-Origin', origin)
        handler.send_header('Vary', 'Origin')

    handler.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
    handler.send_header(
        'Access-Control-Allow-Headers',
        'Content-Type, X-Api-Key, Authorization, X-Telegram-Bot-Api-Secret-Token',
    )
