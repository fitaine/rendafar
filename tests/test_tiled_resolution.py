"""
What the Resolution column means for a tiled render.

Reported live: two resumed 120k jobs showed "4000×4000" and "5000×5000" while
every tile in their folders was 4096×4096 and the finished images are
120000×120000.

The column was showing the .blend's own resolution for every job type. For a
CLASSIC render that is exactly right — it is what comes out. For a TILED render
it is a number the render will never produce: the script reads the scene
resolution for its ASPECT RATIO only, then sets the scene to one tile per
render and assembles the grid. A 2000×2000 @200% scene therefore displayed
"4000×4000" and meant nothing.

The grid is computed with the job's OWN pinned script, so the figure on screen
cannot drift from the figure the render uses.

    python tests\test_tiled_resolution.py
"""

import os
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

import config
from desktop.jobs_table import JobsTable, COL

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


TMP = tempfile.mkdtemp(prefix="tiled_res_")
BLEND = os.path.join(TMP, "scene.blend")
open(BLEND, "wb").write(b"x")

SCRIPT = config.DEFAULT_TILED_SCRIPT


def info_for(res_x, res_y, pct=100):
    """What blend_info.read() gives back, for a scene of this shape."""
    return {"camera": "Camera", "cameras": ["Camera"],
            "resolution_x": res_x, "resolution_y": res_y,
            "pixel_aspect_x": 1.0, "pixel_aspect_y": 1.0,
            "final_x": res_x * pct // 100, "final_y": res_y * pct // 100,
            "samples": 3000, "file_format": "JPEG",
            "frame_start": 1, "frame_end": 1}


_alive = []          # Qt deletes a table's items with the table; keep them


def cell(job, info, col="resolution"):
    t = JobsTable()
    _alive.append(t)
    t._blend[BLEND] = (t._mtime(BLEND), info)
    t.set_jobs([job])
    app.processEvents()
    return t.item(0, COL[col])


def tiled(long_edge=120000, tile_long=4096):
    return {"id": "job_0002", "name": "Tiled", "type": "tiled", "state": "queued",
            "blend": BLEND, "script": SCRIPT,
            "config": {"LONG_EDGE": long_edge, "TILE_LONG": tile_long,
                       "FILE_FORMAT": "WEBP"}}


print("\n=== 1. a tiled job shows what it will actually produce ===")
# The exact case reported: 2000x2000 at 200% -> the table used to say 4000x4000.
it = cell(tiled(), info_for(2000, 2000, pct=200))
check("a 120k tiled job shows the assembled size",
      it.text() == "120000×120000", f"got {it.text()!r}")
check("...not the .blend's own 4000×4000",
      "4000" not in it.text(),
      "that number is used for aspect ratio only and is never rendered")

tip = it.toolTip()
check("the tooltip gives the tile grid",
      "30×30 tiles of 4096×4096" in tip, f"got {tip!r}")
check("...and says what the .blend's resolution is actually for",
      "aspect ratio" in tip, f"got {tip!r}")

print("\n=== 2. the scene's resolution only decides the shape ===")
# Both are square, so both must give the same square result despite very
# different pixel counts - this is the whole point of the fix.
a = cell(tiled(), info_for(2000, 2000, pct=200)).text()
b = cell(tiled(), info_for(5000, 5000)).text()
check("2000² @200% and 5000² @100% agree (both square)",
      a == b == "120000×120000", f"got {a!r} and {b!r}")

# A portrait scene puts the long edge on the other axis.
p = cell(tiled(), info_for(1080, 1350)).text()
check("a portrait scene puts the long edge on height",
      p == "96000×120000", f"got {p!r}")

print("\n=== 3. it follows the job's settings ===")
half = cell(tiled(long_edge=60000), info_for(2000, 2000, pct=200)).text()
check("halving LONG_EDGE halves the assembled size",
      half == "60000×60000", f"got {half!r}")

big = cell(tiled(tile_long=8192), info_for(2000, 2000, pct=200))
check("a bigger tile means fewer tiles, same image",
      big.text() == "120000×120000" and "15×15 tiles of 8192×8192" in big.toolTip(),
      f"got {big.text()!r} / {big.toolTip()!r}")

print("\n=== 4. classic renders are untouched ===")
classic = {"id": "job_0001", "name": "Anim", "type": "classic", "state": "queued",
           "blend": BLEND, "script": SCRIPT, "config": {"MODE": "animation"}}
it = cell(classic, info_for(1242, 2208))
check("a classic job still shows the .blend's own resolution",
      it.text() == "1242×2208", f"got {it.text()!r}")
check("...and says so", "Comes from the .blend" in it.toolTip(), it.toolTip())

print("\n=== 5. it degrades quietly when it cannot know ===")
broken = tiled()
broken["script"] = os.path.join(TMP, "no_such_script.py")
it = cell(broken, info_for(2000, 2000, pct=200))
check("a missing pinned script shows … rather than a wrong number",
      it.text() == "…", f"got {it.text()!r}")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
