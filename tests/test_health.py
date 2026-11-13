"""
"Is the port open" is not "is Rendafar up".

A stranger's Python HTTP server sat on 8765 and answered 501 to every GET.
The desktop app took the open port as proof Rendafar was running, never
started server.py, and showed an empty queue while four jobs waited on disk.
boot_check would have drawn the same conclusion and left an interrupted
render unresumed.

    python tests\test_health.py
"""

import http.server
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import health

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def serve(handler):
    """Run `handler` on a free port, pointed at by config.PORT."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    config.PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Sink(http.server.BaseHTTPRequestHandler):
    """The real culprit: no do_GET at all, so BaseHTTP answers 501."""
    def log_message(self, *a):
        pass


class Rendafar(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"busy": False, "queue": [], "queue_idle": True,
                           "job": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class OtherJson(http.server.BaseHTTPRequestHandler):
    """Answers 200 with valid JSON of somebody else's shape."""
    def do_GET(self):
        body = json.dumps({"status": "ok", "service": "not-rendafar"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


print("\n=== 1. nothing listening ===")
srv = serve(Sink)
port = config.PORT
srv.shutdown()
srv.server_close()
check("a closed port is not Rendafar", health.server_alive(timeout=1) is False)

print("\n=== 2. a stranger that 501s every GET ===")
srv = serve(Sink)
check("501 from a foreign server is not Rendafar",
      health.server_alive(timeout=2) is False,
      "this is the bug: an open port read as 'already running'")
srv.shutdown(); srv.server_close()

print("\n=== 3. valid JSON, wrong shape ===")
srv = serve(OtherJson)
check("another service's JSON is not Rendafar",
      health.server_alive(timeout=2) is False)
srv.shutdown(); srv.server_close()

print("\n=== 4. the real thing ===")
srv = serve(Rendafar)
check("a Rendafar-shaped /api/status is Rendafar",
      health.server_alive(timeout=2) is True)
srv.shutdown(); srv.server_close()

print(f"\n{sum(results)}/{len(results)} passed\n")
sys.exit(0 if all(results) else 1)
