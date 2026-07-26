"""
Phase 6: the desktop-app shell and the PWA.

Covers the Quit endpoint (the only way out when there is no console) and the
files Chrome needs before it will offer "Install app".

    python tests\test_app_shell.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="appshell_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.PORT = 8797
config.PUBLIC_HOSTNAME = ""          # guard off for these tests
config.ensure_dirs()

import importlib                     # noqa: E402
import server as server_mod          # noqa: E402
importlib.reload(server_mod)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def post(path, payload=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{config.PORT}{path}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get(path):
    try:
        r = urllib.request.urlopen(
            f"http://127.0.0.1:{config.PORT}{path}", timeout=5)
        return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""


srv = ThreadingHTTPServer(("127.0.0.1", config.PORT), server_mod.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)

print("\n=== 1. PWA files are served ===")
code, body, ctype = get("/static/manifest.webmanifest")
check("manifest served", code == 200, f"HTTP {code}")
man = json.loads(body) if code == 200 else {}
check("standalone display", man.get("display") == "standalone", str(man.get("display")))
check("has 192 and 512 icons",
      {i["sizes"] for i in man.get("icons", [])} >= {"192x192", "512x512"})
check("has a maskable icon (Android adaptive)",
      any(i.get("purpose") == "maskable" for i in man.get("icons", [])))
check("start_url is the root", man.get("start_url") == "/")

for f in ("/static/icon-192.png", "/static/icon-512.png", "/static/rendafar.ico"):
    code, body, _ = get(f)
    check(f"{f} served", code == 200 and len(body) > 500, f"HTTP {code}, {len(body)}B")

code, body, _ = get("/static/sw.js")
check("service worker served", code == 200)
sw = body.decode()
check("service worker caches NOTHING",
      "caches.put" not in sw and "cache.add" not in sw,
      "a cached render status would be a convincing lie")
check("service worker has a fetch handler (needed to be installable)",
      "addEventListener('fetch'" in sw)

print("\n=== 2. index.html wires up the PWA ===")
code, body, _ = get("/")
html = body.decode("utf-8", "replace")
check("manifest linked", 'rel="manifest"' in html)
check("icon linked", "rendafar.ico" in html)
check("theme colour matches the app background", '#0B0F14' in html)
check("has a Quit button", "quitApp(" in html)

print("\n=== 3. Quit is local-only ===")


def status_with_host(host):
    req = urllib.request.Request(f"http://127.0.0.1:{config.PORT}/api/status")
    req.add_header("Host", host)
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


check("local request -> Quit offered", status_with_host("127.0.0.1")["local"] is True)
check("remote request -> Quit hidden",
      status_with_host("render.example.com")["local"] is False,
      "quitting from the phone could not be undone remotely")

print("\n=== 4. Quit refuses while a render is running ===")


class FakeRunner:
    busy = False
    def is_busy(self):  return self.busy
    def status(self):   return {"job": {"name": "Ecrins 120k"}}
    def log_tail(self, n=0): return []


stopped = {"called": False}
real_runner = server_mod.controller.runner
server_mod.controller.runner = FakeRunner()
server_mod.controller.stop = lambda: stopped.__setitem__("called", True)

server_mod.controller.runner.busy = True
code, d = post("/api/quit")
check("refuses with 409 while rendering", code == 409, f"HTTP {code}")
check("says which job", d.get("job") == "Ecrins 120k", str(d.get("job")))
check("explains why", "unsupervised" in (d.get("error") or ""), str(d.get("error")))
check("did NOT stop the render", not stopped["called"])


def port_alive():
    """Liveness = the socket still accepts. Any HTTP status counts as alive."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", config.PORT)) == 0


check("server still alive after a refused quit", port_alive())

print("\n=== 5. force quit stops the render first ===")
code, d = post("/api/quit", {"force": True})
check("accepts force", d.get("quit") is True, str(d))
check("stopped the render before quitting", stopped["called"])
time.sleep(1.0)
check("server shut down", not port_alive())

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
