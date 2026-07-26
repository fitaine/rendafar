"""
End-to-end test of the queue + runner against a real Blender render.

Renders a throwaway 512px scene on the CPU — never touches the GPU or any real
project file.  Queue state is redirected to a temp folder so the live queue is
untouched.

    python tests\test_queue_runner.py
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="renderfarm_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()

import paths                      # noqa: E402
import queue_manager as qm        # noqa: E402
from controller import Controller # noqa: E402
from runner import pid_alive      # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))
    return ok


def make_blend():
    """A minimal Cycles scene: 4 samples, 100x100, so tiles render in ~a second."""
    blend = os.path.join(TMP, "testscene.blend")
    expr = (
        "import bpy;"
        "s=bpy.context.scene;"
        "s.render.engine='CYCLES';"
        "s.cycles.samples=4;"
        "s.cycles.use_adaptive_sampling=False;"
        "s.render.resolution_x=100; s.render.resolution_y=100;"
        f"bpy.ops.wm.save_as_mainfile(filepath=r'{blend}')"
    )
    subprocess.run([config.BLENDER_EXE, "--background", "--factory-startup",
                    "--python-expr", expr],
                   capture_output=True, text=True, timeout=180)
    return blend if os.path.isfile(blend) else None


def wait_idle(c, timeout=900):
    """
    Wait until the whole queue is drained.

    Not `is_busy()`: the queue auto-advances, and between two jobs there is a
    brief window where no process is running but work remains.  queue_idle
    accounts for that.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if c.status()["queue_idle"]:
            time.sleep(1.0)               # let _watch finish writing state
            if c.status()["queue_idle"]:
                return True
        time.sleep(1)
    return False


print("\n=== building a throwaway test scene ===")
blend = make_blend()
if not check("test .blend created", blend is not None):
    sys.exit(1)

CFG = {"LONG_EDGE": 512, "TILE_LONG": 128, "USE_GPU": False,
       "FILE_FORMAT": "JPEG", "SKIP_EXISTING": True}

print("\n=== 1. queue basics ===")
j1 = qm.create_job(blend, name="first",  cfg=dict(CFG))
j2 = qm.create_job(blend, name="second", cfg=dict(CFG))
check("ids increment", (j1["id"], j2["id"]) == ("job_0001", "job_0002"),
      f"{j1['id']}, {j2['id']}")
check("script pinned to a frozen snapshot",
      "frozen" in j1["script"] and os.path.isfile(j1["script"]), j1["script"])
check("blend referenced in place, not copied", j1["blend"] == os.path.abspath(blend))
check("queue order", [j["id"] for j in qm.all_jobs()] == ["job_0001", "job_0002"])
qm.move("job_0002", -1)
check("move up reorders", [j["id"] for j in qm.all_jobs()] == ["job_0002", "job_0001"])
qm.move("job_0002", 1)
check("move down reorders", [j["id"] for j in qm.all_jobs()] == ["job_0001", "job_0002"])

print("\n=== 2. render a job to completion ===")
c = Controller()
started = c.start_next()
check("start_next picked the first queued job", started and started["id"] == "job_0001")
check("job marked running", qm.load_job("job_0001")["state"] == qm.RUNNING)

try:
    c.runner.start("job_0002")
    check("second render refused while busy", False, "no exception raised")
except RuntimeError:
    check("second render refused while busy", True)

check("blender pid is alive", pid_alive(c.runner.status()["pid"]))

# stdout must stream while the render runs, not arrive in one lump at the end
live = False
for _ in range(40):
    if c.runner.log_tail(50):
        live = True
        break
    time.sleep(1)
check("stdout streams live while rendering", live,
      f"{len(c.runner.log_tail(9999))} lines buffered")

# wait for job 1 (and the auto-started job 2) to finish
ok = wait_idle(c)
check("render finished", ok)

state1 = qm.load_job("job_0001")["state"]
check("job_0001 done", state1 == qm.DONE, f"state={state1}")

prog = paths.progress(qm.load_job("job_0001"))
check("all tiles rendered", prog and prog["done"] == prog["total"] == 16,
      f"{prog['done']}/{prog['total']}" if prog else "no manifest")
check("tiles written next to the .blend, not into app/",
      paths.job_output_dir(qm.load_job("job_0001")).startswith(TMP)
      and os.path.isdir(paths.job_output_dir(qm.load_job("job_0001"))))
check("log file written",
      os.path.getsize(os.path.join(config.LOGS_DIR, "job_0001.log")) > 500)
check("log contains blender tile output",
      "tile_r0000_c0000" in open(os.path.join(config.LOGS_DIR, "job_0001.log"),
                                 encoding="utf-8", errors="replace").read())

print("\n=== 3. queue auto-advance ===")
check("job_0002 auto-started and finished",
      qm.load_job("job_0002")["state"] == qm.DONE,
      f"state={qm.load_job('job_0002')['state']}")

print("\n=== 4. stop kills immediately, resume continues ===")
# Its own RENDER_NAME so it gets its own output folder: jobs sharing a .blend
# AND a RENDER_NAME share tiles, which is what let job_0002 finish instantly.
# 256 tiles so there is a real render to interrupt.
SLOW = dict(CFG, LONG_EDGE=2048, TILE_LONG=128, RENDER_NAME="stoptest")
TILES = 256
j3 = qm.create_job(blend, name="stoppable", cfg=SLOW)
c.start(j3["id"])
time.sleep(12)                       # let a few tiles land
pid = c.runner.status()["pid"]
stopped = c.stop()
check("stop() reported a kill", stopped)
wait_idle(c, timeout=60)
check("blender process is gone", not pid_alive(pid))
check("job marked stopped", qm.load_job(j3["id"])["state"] == qm.STOPPED,
      f"state={qm.load_job(j3['id'])['state']}")

mid = paths.progress(qm.load_job(j3["id"]))
check("some tiles rendered before the stop", mid and 0 < mid["done"] < TILES,
      f"{mid['done']}/{TILES}" if mid else "no manifest")
check("queue did NOT auto-advance after a user stop", not c.runner.is_busy())

qm.resume(j3["id"])
check("resume re-queues at the front",
      qm.load_job(j3["id"])["state"] == qm.QUEUED
      and qm.all_jobs()[0]["id"] == j3["id"])
script_before = qm.load_job(j3["id"])["script"]
c.start_next()
wait_idle(c)
after = qm.load_job(j3["id"])
check("resumed job completed", after["state"] == qm.DONE, f"state={after['state']}")
check("resume did not rewrite the pinned script", after["script"] == script_before)
fin = paths.progress(after)
check(f"all {TILES} tiles present after resume", fin and fin["done"] == TILES,
      f"{fin['done']}/{TILES}" if fin else "no manifest")
check("resume re-rendered only what was missing", fin["done"] > mid["done"])

print("\n=== 5. failure detection ===")
jbad = qm.create_job(blend, name="bad-config", cfg={"LONG_EDGE": 512,
                                                    "USE_GPU": False,
                                                    "NOT_A_REAL_KEY": 1})
try:
    c.start(jbad["id"])
    wait_idle(c, timeout=120)
    st = qm.load_job(jbad["id"])["state"]
    check("bad job config -> failed, not done", st == qm.FAILED, f"state={st}")
except Exception as e:
    check("bad job config -> failed, not done", False, str(e))

print("\n=== 6. power-cut recovery ===")
jx = qm.create_job(blend, name="was-running", cfg=dict(CFG))
qm.set_state(jx["id"], qm.RUNNING)
st = qm.load_state(); st["running"] = jx["id"]; st["running_pid"] = 999999
qm.save_state(st)
found = Controller().recover()
check("stale running job detected as interrupted", found == [jx["id"]], str(found))
check("state is 'interrupted'", qm.load_job(jx["id"])["state"] == qm.INTERRUPTED)
check("interrupted job is resumable", qm.load_job(jx["id"])["state"] in qm.RESUMABLE)
check("pid check rejects a dead pid", not pid_alive(999999))

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
print(f"  temp dir: {TMP}")
sys.exit(0 if all(results) else 1)
