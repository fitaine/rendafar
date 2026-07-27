"""
The FILE_FORMAT override on classic renders, proven by rendering.

An API test only shows the setting was stored. This runs Blender and checks
the file that comes out, because the format decides the extension, and the
extension decides which frames count as already rendered on resume.

    python tests\test_classic_format.py
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="fmt_test_")
config.QUEUE_DIR = os.path.join(TMP, "queue")
config.LOGS_DIR = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()

import paths                          # noqa: E402
import queue_manager as qm            # noqa: E402
from controller import Controller     # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def wait_idle(c, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if c.status()["queue_idle"]:
            time.sleep(1)
            if c.status()["queue_idle"]:
                return True
        time.sleep(1)
    return False


print("\n=== a scene that saves PNG ===")
blend = os.path.join(TMP, "fmt.blend")
out = os.path.join(TMP, "out", "f_")
os.makedirs(os.path.dirname(out), exist_ok=True)
expr = (
    "import bpy;s=bpy.context.scene;"
    "s.render.engine='CYCLES';s.cycles.samples=2;"
    "s.cycles.use_adaptive_sampling=False;s.cycles.device='CPU';"
    "s.render.resolution_x=64;s.render.resolution_y=64;"
    "s.render.resolution_percentage=100;"
    "s.render.image_settings.file_format='PNG';"
    f"s.render.filepath=r'{out}';"
    "s.frame_start=1;s.frame_end=1;"
    f"bpy.ops.wm.save_as_mainfile(filepath=r'{blend}')"
)
subprocess.run([config.BLENDER_EXE, "-b", "--factory-startup", "--python-expr", expr],
               capture_output=True, timeout=300)
check("test scene written", os.path.isfile(blend))

c = Controller()

print("\n=== with no override it writes the .blend's PNG ===")
j = qm.create_job(blend, name="default", job_type="classic",
                  cfg={"MODE": "still", "FRAME": 1, "DEVICE": "CPU"})
c.start(j["id"])
wait_idle(c)
made = sorted(os.listdir(os.path.dirname(out)))
check("a PNG came out", any(f.lower().endswith(".png") for f in made), str(made))

print("\n=== overriding FILE_FORMAT changes the file that appears ===")
out2 = os.path.join(TMP, "out_jpg", "f_")
os.makedirs(os.path.dirname(out2), exist_ok=True)
j2 = qm.create_job(blend, name="jpeg", job_type="classic",
                   cfg={"MODE": "still", "FRAME": 1, "DEVICE": "CPU",
                        "OUTPUT_DIR": out2, "FILE_FORMAT": "JPEG"})
check("the job pins the 2026-07-27 script or later",
      "2026-07-27" in j2["script"] or "FILE_FORMAT" in
      (paths.script_overridable(j2["script"]) or set()), j2["script"])
c.start(j2["id"])
wait_idle(c)
state = qm.load_job(j2["id"])["state"]
check("the render finished", state == qm.DONE, f"state={state}")

made2 = sorted(os.listdir(os.path.dirname(out2)))
check("a JPEG came out, not a PNG",
      any(f.lower().endswith((".jpg", ".jpeg")) for f in made2)
      and not any(f.lower().endswith(".png") for f in made2), str(made2))

log = open(os.path.join(config.LOGS_DIR, f"{j2['id']}.log"),
           encoding="utf-8", errors="replace").read()
check("the script says the format was overridden",
      "overridden by job" in log,
      [l for l in log.splitlines() if "format" in l.lower()][:3])

print("\n=== a format Blender does not know fails loudly ===")
j3 = qm.create_job(blend, name="bogus", job_type="classic",
                   cfg={"MODE": "still", "FRAME": 1, "DEVICE": "CPU",
                        "FILE_FORMAT": "NOT_A_FORMAT"})
c.start(j3["id"])
wait_idle(c, 180)
check("the job failed rather than rendering something unexpected",
      qm.load_job(j3["id"])["state"] == qm.FAILED,
      f"state={qm.load_job(j3['id'])['state']}")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
