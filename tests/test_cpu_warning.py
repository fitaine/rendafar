"""
A render left on the CPU is flagged before it runs.

Old .blends and slips leave Cycles on CPU, and the classic script obeys the
.blend, so such a render simply runs many times slower with nothing to say why.
The .blend probe already reads the device; these checks are that it reaches
the person: as a caveat when the job is added, and in red in the Jobs table.

Only the unasked-for case is flagged. A job whose DEVICE says CPU chose it, and
the tiled script switches to GPU itself.

No Blender needed — the probe's output is written by hand.

    python tests\\test_cpu_warning.py
"""

import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

from PySide6 import QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

import blend_info                                      # noqa: E402
import config                                          # noqa: E402
from desktop import theme                              # noqa: E402
from desktop.jobs_table import JobsTable, COL          # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def scene(name, device, engine="CYCLES"):
    return {"scene": name, "camera": "Cam", "cameras": ["Cam"],
            "engine": engine, "device": device, "samples": 1000,
            "resolution_x": 100, "resolution_y": 100, "resolution_percentage": 100,
            "pixel_aspect_x": 1.0, "pixel_aspect_y": 1.0,
            "final_x": 100, "final_y": 100, "file_format": "PNG",
            "frame_start": 1, "frame_end": 1, "frame_step": 1}


def blend(active, *scenes):
    info = dict(next(s for s in scenes if s["scene"] == active))
    info.update({"active_scene": active, "scenes": list(scenes)})
    return info


GPU_ACTIVE = blend("Main", scene("Main", "GPU"), scene("Old", "CPU"))
CPU_ACTIVE = blend("Main", scene("Main", "CPU"))
EEVEE = blend("Main", scene("Main", None, engine="BLENDER_EEVEE_NEXT"))

W = blend_info.cpu_warning

print("\n=== 1. which jobs are flagged ===")
check("a classic job on a CPU scene is flagged",
      W(CPU_ACTIVE, "classic", {"MODE": "still"}))
check("...and the warning names the scene",
      "Main" in (W(CPU_ACTIVE, "classic", {}) or ""))
check("a GPU scene is not", W(GPU_ACTIVE, "classic", {}) is None)
check("the SCENE the job picked counts, not the active one",
      W(GPU_ACTIVE, "classic", {"SCENE": "Old"}) is not None
      and W(GPU_ACTIVE, "classic", {"SCENE": "Main"}) is None)
check("a job that chose the CPU is left alone",
      W(CPU_ACTIVE, "classic", {"DEVICE": "CPU"}) is None)
check("...as is one overridden to GPU",
      W(CPU_ACTIVE, "classic", {"DEVICE": "GPU"}) is None)
check("a tiled job is not flagged — its script switches to GPU itself",
      W(CPU_ACTIVE, "tiled", {}) is None)
check("an engine with no device is not flagged", W(EEVEE, "classic", {}) is None)
check("an unreadable .blend is not flagged",
      W({"error": "file not found"}, "classic", {}) is None)

tiled_src = open(config.DEFAULT_TILED_SCRIPT, encoding="utf-8").read()
check("the tiled script really does default to GPU",
      "USE_GPU        = True" in tiled_src,
      "if this changes, tiled jobs need the warning too")

print("\n=== 2. the Jobs table ===")
BLEND = os.path.join(APP, "tests", "_cpu_probe.blend")
open(BLEND, "wb").write(b"x")
_alive = []


def samples_cell(info, cfg, job_type="classic"):
    t = JobsTable()
    _alive.append(t)
    t._blend[BLEND] = (t._mtime(BLEND), info)
    t.set_jobs([{"id": "j1", "name": "j", "type": job_type, "state": "queued",
                 "blend": BLEND, "script": config.DEFAULT_SCRIPTS[job_type],
                 "config": cfg}])
    app.processEvents()
    return t.item(0, COL["samples"])


c = samples_cell(CPU_ACTIVE, {"MODE": "still"})
check("a CPU render says so in the Samples cell", c.text() == "1000 · CPU",
      f"got {c.text()!r}")
check("...in red", c.foreground().color().name().lower() == theme.ALERT.lower(),
      c.foreground().color().name())
check("...with the reason on hover", "CPU" in (c.toolTip() or ""), c.toolTip())

c = samples_cell(GPU_ACTIVE, {"MODE": "still"})
check("a GPU render is shown as before", c.text() == "1000", f"got {c.text()!r}")

c = samples_cell(CPU_ACTIVE, {"MODE": "still", "DEVICE": "CPU"})
check("a chosen CPU is shown as before", c.text() == "1000", f"got {c.text()!r}")

os.remove(BLEND)
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
