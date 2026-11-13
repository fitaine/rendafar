"""
Tiled still, end to end through the Runner, in an isolated temp queue:
progress counts tiles, Stop keeps finished tiles, resume skips them, and the
job only settles 'done' once the stitched + composited image exists.

    python tests\\test_tiled_still.py <small.blend>

Needs a real .blend; keep it small (a few hundred px, low samples).
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

TMP = tempfile.mkdtemp(prefix="tiled_still_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.AUTO_START_NEXT = False
config.ensure_dirs()

import paths                       # noqa: E402
import queue_manager as qm         # noqa: E402
from controller import Controller  # noqa: E402
from runner import cli_still_frame  # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


blend = sys.argv[1]
out = os.path.join(TMP, "out") + os.sep + "still_"
job = qm.create_job(blend, name="tiled", job_type="classic",
                    cfg={"MODE": "still", "TILE_SIZE": 200, "OUTPUT_DIR": out})
check("job pins the tiles snapshot", job["script"].endswith("2026-11-12_tiles.py"), job["script"])
check("runner does not use -f for a tiled still", cli_still_frame(job) is None)

c = Controller()
c.start(job["id"])


def wait(cond, limit=900):
    t = time.time()
    while time.time() - t < limit:
        if cond():
            return True
        time.sleep(2)
    return False


# Stop after the first tile.
wait(lambda: (paths.read_manifest(job) or {}).get("frames_done", 0) >= 1)
c.stop(after_current=False)
wait(lambda: not c.runner.is_busy(), 120)
m = paths.read_manifest(job) or {}
check("manifest counts tiles", m.get("unit") == "tiles" and m.get("total") == 6, m.get("total"))
tile_dir = m.get("tile_dir", "")
done_before = len([f for f in os.listdir(tile_dir) if f.startswith("tile_") and f.endswith(".exr")
                   and "_partial" not in f]) if os.path.isdir(tile_dir) else 0
check("stopped job keeps its finished tiles", done_before >= 1, done_before)
check("stopped job is not done", qm.load_job(job["id"])["state"] != qm.DONE,
      qm.load_job(job["id"])["state"])

# Resume to the end.
c.start(job["id"])
wait(lambda: not c.runner.is_busy() and qm.load_job(job["id"])["state"] != qm.RUNNING, 1800)
j = qm.load_job(job["id"])
m = paths.read_manifest(job) or {}
log = open(os.path.join(config.LOGS_DIR, f"{job['id']}.log"), encoding="utf-8", errors="replace").read() \
    if os.path.isfile(os.path.join(config.LOGS_DIR, f"{job['id']}.log")) else ""
check("job settles done", j["state"] == qm.DONE, j["history"][-1])
check("resume skipped finished tiles", f"Resume: {done_before}/6" in log or "Resume:" in log)
check("final image exists", os.path.isfile(m.get("final", "")), m.get("final"))
check("manifest complete", m.get("frames_done") == 6 and m.get("grid") == "1" * 6, m.get("grid"))

left = [f for f in os.listdir(tile_dir) if f.startswith("tile_")]
check("tiles cleaned up, stitched.exr kept",
      not left and os.path.isfile(os.path.join(tile_dir, "stitched.exr")), left)

# Redo the compositing: delete the final image and resume.
os.remove(m["final"])
t0 = time.time()
c.start(job["id"])
wait(lambda: not c.runner.is_busy() and qm.load_job(job["id"])["state"] != qm.RUNNING, 600)
j = qm.load_job(job["id"])
log2 = open(os.path.join(config.LOGS_DIR, f"{job['id']}.log"), encoding="utf-8", errors="replace").read()
check("recomposite settles done", j["state"] == qm.DONE, j["history"][-1])
check("final image back", os.path.isfile(m["final"]))
check("nothing re-rendered", "compositing again from stitched.exr" in log2)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed   ({TMP})\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
