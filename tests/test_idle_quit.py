"""
Closing the window should close the app.

Rendafar runs hidden when launched from the desktop icon, so the window's X
only closes the window. The server therefore shuts itself down once no browser
has polled for a while and nothing is rendering — with the hard rule that a
running render always keeps it alive.

    python tests\test_idle_quit.py
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="idlequit_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.PORT = 8795
config.PUBLIC_HOSTNAME = ""
config.IDLE_QUIT_S = 3          # 90 in real use; short so the test is quick
config.ensure_dirs()

import importlib                    # noqa: E402
import server as server_mod         # noqa: E402
importlib.reload(server_mod)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def alive():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", config.PORT)) == 0


def poll():
    """What an open window does every 3 seconds."""
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{config.PORT}/api/status", timeout=5).read()
        return True
    except Exception:
        return False


class FakeRunner:
    busy = False
    def is_busy(self):  return self.busy
    def status(self):
        return {"busy": self.busy, "job_id": None, "job": None, "pid": None,
                "live": None, "session_s": None, "progress": None}
    def log_tail(self, n=0): return []


server_mod.controller.runner = FakeRunner()

srv = ThreadingHTTPServer(("127.0.0.1", config.PORT), server_mod.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
threading.Thread(target=server_mod.idle_watchdog, args=(srv,), daemon=True).start()
time.sleep(0.4)

print("\n=== 1. a window keeps it alive ===")
ok = True
for _ in range(6):              # poll across more than IDLE_QUIT_S
    time.sleep(1)
    if not poll():
        ok = False
        break
check("stays up while a window is polling", ok and alive(),
      f"polled for 6s with IDLE_QUIT_S={config.IDLE_QUIT_S}")

print("\n=== 2. a render keeps it alive even with no window ===")
server_mod.controller.runner.busy = True
poll()
time.sleep(config.IDLE_QUIT_S + 3)      # nobody polling at all
check("a running render outranks tidiness", alive(),
      "the render must stay supervised and reachable from the phone")

print("\n=== 3. window closed, nothing rendering -> it closes itself ===")
server_mod.controller.runner.busy = False
poll()                                   # last window seen now
check("still up immediately after the last poll", alive())
time.sleep(config.IDLE_QUIT_S + 4)
check("closed itself once nobody was watching", not alive(),
      "so the window's X behaves like an X")

print("\n=== 4. it is opt-in, not the default ===")
src = open(os.path.join(config.APP_DIR, "server.py"), encoding="utf-8").read()
check("watchdog only starts in app mode",
      'RENDAFAR_APP_MODE' in src and 'idle_watchdog' in src,
      "a console launch, and the power-cut auto-resume, stay up")
launcher = open(os.path.join(config.APP_DIR, "app_launcher.pyw"),
                encoding="utf-8").read()
check("the desktop launcher sets app mode",
      'RENDAFAR_APP_MODE"] = "1"' in launcher)
loop = open(os.path.join(config.APP_DIR, "RUN SERVER LOOP.bat"),
            encoding="utf-8", errors="replace").read()
check("the auto-resume launcher does NOT set app mode",
      "RENDAFAR_APP_MODE" not in loop,
      "after a power cut it should stay up, not vanish 90s later")

print("\n=== 5. no Quit button in the UI ===")
html = open(os.path.join(config.APP_DIR, "static", "index.html"),
            encoding="utf-8").read()
check("Quit button removed", "Quit Rendafar</button>" not in html,
      "the window's X is the way out")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
