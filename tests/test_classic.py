"""
End-to-end test of classic (non-tiled) renders.

The central claim under test: **the .blend is in charge**.  The test scene is
built with deliberately awkward settings — 160×120 at 50% (so 80×60 actual),
fixed 3 samples, PNG, a nested output folder, frames 1–8 — and the renders must
come out matching exactly, with nothing overridden by the app.

    python tests\test_classic.py
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="classic_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()

import paths                       # noqa: E402
import queue_manager as qm         # noqa: E402
from controller import Controller  # noqa: E402

results = []

# What the .blend will be saved with. The render must match these exactly.
RES_X, RES_Y, PCT = 160, 120, 50
EXPECT = (RES_X * PCT // 100, RES_Y * PCT // 100)      # 80 × 60
SAMPLES = 3
F_START, F_END = 1, 8
NFRAMES = F_END - F_START + 1


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))
    return ok


def make_blend():
    blend   = os.path.join(TMP, "classic.blend")
    outdir  = os.path.join(TMP, "renders", "seq")     # nested on purpose
    os.makedirs(outdir, exist_ok=True)
    expr = (
        "import bpy;"
        "s=bpy.context.scene;"
        "s.render.engine='CYCLES';"
        f"s.cycles.samples={SAMPLES};"
        "s.cycles.use_adaptive_sampling=False;"
        "s.cycles.device='CPU';"
        f"s.render.resolution_x={RES_X}; s.render.resolution_y={RES_Y};"
        f"s.render.resolution_percentage={PCT};"
        "s.render.image_settings.file_format='PNG';"
        f"s.render.filepath=r'{os.path.join(outdir, 'fr_')}';"
        f"s.frame_start={F_START}; s.frame_end={F_END};"
        f"bpy.ops.wm.save_as_mainfile(filepath=r'{blend}')"
    )
    subprocess.run([config.BLENDER_EXE, "--background", "--factory-startup",
                    "--python-expr", expr], capture_output=True, timeout=300)
    return (blend if os.path.isfile(blend) else None), outdir


def wait_idle(c, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if c.status()["queue_idle"]:
            time.sleep(1.0)
            if c.status()["queue_idle"]:
                return True
        time.sleep(1)
    return False


def frames_on_disk(outdir):
    return sorted(f for f in os.listdir(outdir) if f.endswith(".png"))


print("\n=== building the test scene ===")
blend, OUT = make_blend()
if not check("test .blend created", blend is not None):
    sys.exit(1)
print(f"        {RES_X}×{RES_Y} @ {PCT}%  →  {EXPECT[0]}×{EXPECT[1]}, "
      f"{SAMPLES} samples, PNG, frames {F_START}-{F_END}")

c = Controller()

print("\n=== 1. animation: the .blend decides everything ===")
j = qm.create_job(blend, name="anim", job_type="classic",
                  cfg={"MODE": "animation", "DEVICE": "CPU"})
check("classic job pins the classic frozen script",
      "render_classic" in j["script"] and os.path.isfile(j["script"]), j["script"])
c.start_next()
check("job started", c.runner.is_busy() or qm.load_job(j["id"])["state"] != qm.QUEUED)
wait_idle(c)

st = qm.load_job(j["id"])["state"]
check("animation completed", st == qm.DONE, f"state={st}")

files = frames_on_disk(OUT)
check(f"all {NFRAMES} frames rendered", len(files) == NFRAMES, str(files))
check("frames use the .blend's own naming/path",
      files and files[0] == "fr_0001.png", files[0] if files else "none")

from PIL import Image
with Image.open(os.path.join(OUT, files[0])) as im:
    size = im.size
    fmt = im.format
check(f"resolution honours the .blend ({EXPECT[0]}×{EXPECT[1]}, incl. the 50%)",
      size == EXPECT, f"got {size[0]}×{size[1]}")
check("format honours the .blend (PNG)", fmt == "PNG", f"got {fmt}")

log = open(os.path.join(config.LOGS_DIR, f"{j['id']}.log"),
           encoding="utf-8", errors="replace").read()
check("script reports the .blend's samples, not its own",
      f"samples      : {SAMPLES}" in log,
      [l for l in log.splitlines() if "samples" in l][:2])
check("script reports the resolved resolution",
      f"{EXPECT[0]} × {EXPECT[1]} px" in log,
      [l for l in log.splitlines() if "resolution" in l][:2])

p = paths.progress(qm.load_job(j["id"]))
check("progress reports frames, not tiles",
      p and p["unit"] == "frames" and p["done"] == NFRAMES,
      f"{p['done']}/{p['total']} {p['unit']}" if p else "none")
check("manifest kept out of the render folder",
      not os.path.exists(os.path.join(OUT, "manifest.json"))
      and os.path.isfile(paths.classic_manifest_path(qm.load_job(j["id"]))))
check("latest image points at a real frame",
      (lambda lt: lt and os.path.isfile(lt[0]))(paths.latest_tile(qm.load_job(j["id"]))))

print("\n=== 2. resume renders only what is missing ===")
before = {f: os.path.getmtime(os.path.join(OUT, f)) for f in frames_on_disk(OUT)}
for gone in ("fr_0003.png", "fr_0005.png"):
    os.remove(os.path.join(OUT, gone))
check("two frames removed", len(frames_on_disk(OUT)) == NFRAMES - 2)

j2 = qm.create_job(blend, name="anim-resume", job_type="classic",
                   cfg={"MODE": "animation", "DEVICE": "CPU"})
c.start(j2["id"])
wait_idle(c)
check("resume run completed", qm.load_job(j2["id"])["state"] == qm.DONE)
check("missing frames were re-rendered", len(frames_on_disk(OUT)) == NFRAMES)

untouched = [f for f, mt in before.items()
             if f not in ("fr_0003.png", "fr_0005.png")
             and os.path.exists(os.path.join(OUT, f))
             and os.path.getmtime(os.path.join(OUT, f)) == mt]
check("existing frames were NOT re-rendered", len(untouched) == NFRAMES - 2,
      f"{len(untouched)} of {NFRAMES-2} untouched")
log2 = open(os.path.join(config.LOGS_DIR, f"{j2['id']}.log"),
            encoding="utf-8", errors="replace").read()
check("log shows frames being skipped", "SKIP" in log2)

print("\n=== 3. still mode ===")
stilldir = os.path.join(TMP, "renders", "still")
j3 = qm.create_job(blend, name="one frame", job_type="classic",
                   cfg={"MODE": "still", "FRAME": 4, "DEVICE": "CPU",
                        "OUTPUT_DIR": os.path.join(stilldir, "shot_")})
c.start(j3["id"])
wait_idle(c)
check("still completed", qm.load_job(j3["id"])["state"] == qm.DONE,
      f"state={qm.load_job(j3['id'])['state']}")
made = os.path.isdir(stilldir) and [f for f in os.listdir(stilldir) if f.endswith(".png")]
check("exactly one image written", bool(made) and len(made) == 1, str(made))
check("still used the requested frame number",
      bool(made) and "0004" in made[0], str(made))
if made:
    with Image.open(os.path.join(stilldir, made[0])) as im:
        check("still still honours the .blend resolution", im.size == EXPECT,
              f"{im.size}")

print("\n=== 4. stop and resume mid-animation ===")
# Long enough that there is genuinely a render to interrupt: this scene does
# roughly 8 frames a second, so 60 frames finished before stop() was even called.
LONG    = 600
longdir = os.path.join(TMP, "renders", "long")
j4 = qm.create_job(blend, name="stoppable anim", job_type="classic",
                   cfg={"MODE": "animation", "FRAME_START": 1, "FRAME_END": LONG,
                        "DEVICE": "CPU",
                        "OUTPUT_DIR": os.path.join(longdir, "f_")})
c.start(j4["id"])
time.sleep(14)
stopped = c.stop()
check("stop reported a kill", stopped)
wait_idle(c, 120)
check("job marked stopped", qm.load_job(j4["id"])["state"] == qm.STOPPED,
      f"state={qm.load_job(j4['id'])['state']}")
mid = len(os.listdir(longdir)) if os.path.isdir(longdir) else 0
check("some frames rendered before the stop", 0 < mid < LONG, f"{mid}/{LONG}")

qm.resume(j4["id"])
c.start_next()
wait_idle(c)
after = qm.load_job(j4["id"])
check("resumed animation completed", after["state"] == qm.DONE, f"state={after['state']}")
check(f"all {LONG} frames present", len(os.listdir(longdir)) == LONG,
      f"{len(os.listdir(longdir))}/{LONG}")
check("resume picked up where it stopped, not from frame 1",
      len(os.listdir(longdir)) > mid, f"{mid} → {len(os.listdir(longdir))}")
check("pinned script unchanged by resume", after["script"] == j4["script"])

print("\n=== 5. bad config still fails loudly ===")
jbad = qm.create_job(blend, name="bad", job_type="classic",
                     cfg={"SAMPLES": 500})     # not an overridable key
c.start(jbad["id"])
wait_idle(c, 120)
check("unknown key -> failed, not done",
      qm.load_job(jbad["id"])["state"] == qm.FAILED,
      f"state={qm.load_job(jbad['id'])['state']}")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
print(f"  temp dir: {TMP}")
sys.exit(0 if all(results) else 1)
