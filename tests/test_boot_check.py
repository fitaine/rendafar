"""
The logon check must be invisible on an ordinary morning.

Rendafar is a normal app: double-click to run, close to stop. The single
exception is an interrupted render, which must come back without a human. This
tests that the check fires in exactly that case and stays silent otherwise.

    python tests\test_boot_check.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="bootcheck_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()

import boot_check                  # noqa: E402
import queue_manager as qm         # noqa: E402

results = []
launched = []

# Never actually spawn a console during the test.
boot_check.subprocess.Popen = lambda *a, **kw: launched.append(a) or type(
    "P", (), {"pid": 1})()
boot_check.already_running = lambda: False


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


blend = os.path.join(TMP, "x.blend")
open(blend, "wb").write(b"not a real blend")

print("\n=== 1. ordinary boot: nothing was rendering ===")
launched.clear()
check("no state at all -> stays off", boot_check.interrupted_job() is None)
boot_check.main()
check("nothing launched", launched == [])

print("\n=== 2. queued jobs waiting, but none was running ===")
j = qm.create_job(blend, name="waiting", cfg={"LONG_EDGE": 512})
launched.clear()
boot_check.main()
check("a merely queued job does NOT start the app", launched == [],
      "queued work waits for you to open Rendafar")
check("job left untouched", qm.load_job(j["id"])["state"] == qm.QUEUED)

print("\n=== 3. clean shutdown after a finished render ===")
qm.set_state(j["id"], qm.DONE)
st = qm.load_state(); st["running"] = None; st["running_pid"] = None
qm.save_state(st)
launched.clear()
boot_check.main()
check("finished render -> stays off", launched == [])

print("\n=== 4. power cut mid-render ===")
j2 = qm.create_job(blend, name="was rendering", cfg={"LONG_EDGE": 512})
qm.set_state(j2["id"], qm.RUNNING)
st = qm.load_state(); st["running"] = j2["id"]; st["running_pid"] = 4242
qm.save_state(st)
check("interrupted job detected",
      (boot_check.interrupted_job() or {}).get("id") == j2["id"])
launched.clear()
boot_check.main()
check("Rendafar is launched", len(launched) == 1, str(launched))
check("it launches the supervised loop",
      launched and "RUN SERVER LOOP.bat" in launched[0][0][0], str(launched[0][0]) if launched else "")

print("\n=== 5. stale pointer to a job that already finished ===")
qm.set_state(j2["id"], qm.DONE)          # state.json still says running
launched.clear()
boot_check.main()
check("does not start for a job that is actually done", launched == [],
      "guards against a stale state.json pointer")

print("\n=== 6. already up (you launched it yourself) ===")
qm.set_state(j2["id"], qm.RUNNING)
boot_check.already_running = lambda: True
launched.clear()
boot_check.main()
check("does not start a second copy", launched == [])

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
