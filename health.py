"""
Is *Rendafar* on the port, or just something?

Both places that ask "is the server already up" used to open a TCP connection
to config.PORT and treat a successful connect as yes. That answers a different
question — "is anything listening" — and the difference is not academic. An
unrelated Python HTTP server bound 8765 here and answered 501 to every GET:

  * the desktop app's ensure_server() saw the port open, never started
    server.py, and polled the stranger instead. /api/status came back as an
    error page, the poll's JSON parse threw, and the queue panel was simply
    never filled in — which reads on screen as "the queue has been emptied",
    while four jobs sat untouched in queue/.

  * worse, boot_check would have said "Rendafar is already up - leaving it"
    and gone back to sleep, so a render interrupted by a power cut would never
    have resumed. That check existing at all is the reason this app exists.

So identify the thing on the port before believing it. /api/status is the one
endpoint every client already depends on, and its reply is distinctive enough:
`busy` and `queue_idle` together are ours. Anything else — a refusal, an error
page, a JSON body of some other shape, no answer at all — is not Rendafar, and
the caller should start the real server.
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config


def server_alive(timeout=1.5):
    """True only if Rendafar itself answers /api/status on the app's port."""
    url = f"http://127.0.0.1:{config.PORT}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        # Refused, timed out, an error status, or a body that is not JSON.
        return False
    return isinstance(body, dict) and "busy" in body and "queue_idle" in body
