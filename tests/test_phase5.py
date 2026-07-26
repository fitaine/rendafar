"""
Phase 5: notifications, the Cloudflare Access guard, and boot recovery —
including the orphaned-Blender case (server died, Blender didn't).

    python tests\test_phase5.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="phase5_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()

import notify                      # noqa: E402
import queue_manager as qm         # noqa: E402
from controller import Controller  # noqa: E402
from runner import pid_alive       # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))
    return ok


print("\n=== 1. notifications ===")
sent = []
notify.send = lambda text: sent.append(text)          # capture, don't network
job = {"id": "job_0042", "name": "Barre des Écrins 120k",
       "history": [{"state": "failed", "note": "exit 0 but only 3/900 tiles rendered"}]}

notify.job_event("done", job)
check("done message", sent and "finished" in sent[-1] and "job_0042" in sent[-1], sent[-1] if sent else "")
notify.job_event("failed", job)
check("failed message carries the reason", "3/900" in sent[-1], sent[-1])
notify.job_event("interrupted", job)
check("interrupted message", "interrupted" in sent[-1].lower())
notify.job_event("resumed_after_boot", job)
check("auto-resume message", "resumed" in sent[-1].lower())
n = len(sent)
notify.job_event("stopped", job)
check("user stop sends nothing", len(sent) == n)

print("\n=== 2. Cloudflare Access guard ===")
# Start the real server on a test port with a fake public hostname.
config.PORT = 8799
config.PUBLIC_HOSTNAME = "render.example.com"
import controller as controller_mod
import importlib
import server as server_mod
importlib.reload(server_mod)
import threading
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", config.PORT), server_mod.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)


def req(host_header=None, jwt=None):
    r = urllib.request.Request(f"http://127.0.0.1:{config.PORT}/api/status")
    if host_header:
        r.add_header("Host", host_header)
    if jwt:
        r.add_header("Cf-Access-Jwt-Assertion", jwt)
    try:
        return urllib.request.urlopen(r, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


check("local request passes (no guard)", req() == 200)
check("tunnel request WITHOUT Access header -> 403",
      req(host_header="render.example.com") == 403)
check("tunnel request WITH Access header -> 200",
      req(host_header="render.example.com", jwt="x.y.z") == 200)
srv.shutdown()

print("\n=== 3. boot recovery — dead process ===")
blend = os.path.join(TMP, "t.blend")
expr = ("import bpy;"
        f"bpy.ops.wm.save_as_mainfile(filepath=r'{blend}')")
subprocess.run([config.BLENDER_EXE, "-b", "--factory-startup", "--python-expr", expr],
               capture_output=True, timeout=180)

j1 = qm.create_job(blend, name="dead", cfg={"LONG_EDGE": 512, "USE_GPU": False})
qm.set_state(j1["id"], qm.RUNNING)
st = qm.load_state(); st["running"] = j1["id"]; st["running_pid"] = 999999
qm.save_state(st)

events = []
c = Controller(notify=lambda ev, job, note=None: events.append(ev))
found = c.recover()
check("dead pid -> interrupted", found == [j1["id"]]
      and qm.load_job(j1["id"])["state"] == qm.INTERRUPTED)
check("interruption was notified", "interrupted" in events)
qm.remove(j1["id"])

print("\n=== 4. boot recovery — ORPHANED live Blender ===")
# A real blender.exe parked in a sleep: the server 'crashed', Blender lives on.
orphan = subprocess.Popen(
    [config.BLENDER_EXE, "-b", "--factory-startup", "--python-expr",
     "import time; time.sleep(600)"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
check("orphan blender is alive", pid_alive(orphan.pid))

j2 = qm.create_job(blend, name="orphaned", cfg={"LONG_EDGE": 512, "USE_GPU": False})
qm.set_state(j2["id"], qm.RUNNING)
st = qm.load_state(); st["running"] = j2["id"]; st["running_pid"] = orphan.pid
qm.save_state(st)

events.clear()
found = c.recover()
time.sleep(2)
check("orphan detected as interrupted", found == [j2["id"]]
      and qm.load_job(j2["id"])["state"] == qm.INTERRUPTED)
check("orphan blender was killed", not pid_alive(orphan.pid))
note = (qm.load_job(j2["id"])["history"][-1].get("note") or "")
check("history explains the kill", "orphan" in note.lower(), note)

print("\n=== 5. recover_and_resume auto-starts ONLY after an interruption ===")
qm.set_state(j2["id"], qm.INTERRUPTED)      # still interrupted from above
events.clear()
found, started = c.recover_and_resume()     # nothing newly interrupted now...
check("nothing to recover -> nothing started", found == [] and started is None)

j3 = qm.create_job(blend, name="queued-not-started",
                   cfg={"LONG_EDGE": 512, "USE_GPU": False})
found, started = c.recover_and_resume()
check("queued-but-never-started job stays queued",
      started is None and qm.load_job(j3["id"])["state"] == qm.QUEUED)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
