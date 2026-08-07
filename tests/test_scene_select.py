"""
Choosing which scene in a .blend to render.

A .blend can hold several scenes, each with its own cameras, resolution, frame
range and output path. Both render scripts read bpy.context.scene, so they
rendered whichever one happened to be active when the file was saved, with no
way to say otherwise.

The mechanism is one assignment — bpy.context.window.scene — done before
anything else reads the scene, so the rest of both scripts works unchanged.
That it works under -b at all was verified before any of this was written:
bpy.context.window is NOT None in background mode (Blender still creates one
window in the window manager), and a real render of a two-scene file with
SCENE="Beta" produced Beta's 32x48 frame from Beta's camera while the default
produced 64x64.

These checks are the parts that do not need Blender: what the table shows and
offers, and that the plumbing lines up end to end.

    python tests\test_scene_select.py
"""

import os
import sys

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


BLEND = os.path.join(APP, "tests", "_scene_probe.blend")
open(BLEND, "wb").write(b"x")


def scene(name, cam, cams, x, y, samples, fmt="PNG"):
    return {"scene": name, "camera": cam, "cameras": cams,
            "resolution_x": x, "resolution_y": y, "resolution_percentage": 100,
            "pixel_aspect_x": 1.0, "pixel_aspect_y": 1.0,
            "final_x": x, "final_y": y, "samples": samples, "file_format": fmt,
            "frame_start": 1, "frame_end": 100, "frame_step": 1}


MULTI = dict(scene("Alpha", "CamAlpha", ["CamAlpha"], 1920, 1080, 128))
MULTI.update({
    "active_scene": "Alpha",
    "scenes": [scene("Alpha", "CamAlpha", ["CamAlpha"], 1920, 1080, 128),
               scene("Beta", "CamBeta", ["CamBeta", "CamB2"], 1080, 1350, 4096,
                     "JPEG")],
})
SINGLE = dict(scene("Scene", "Camera", ["Camera"], 5000, 5000, 2048))
SINGLE.update({"active_scene": "Scene",
               "scenes": [scene("Scene", "Camera", ["Camera"], 5000, 5000, 2048)]})

_alive = []


def row(info, cfg, job_type="classic", state="queued"):
    t = JobsTable()
    _alive.append(t)
    t._blend[BLEND] = (t._mtime(BLEND), info)
    # The pinned script has to match the type: a tiled row lifts compute_grid
    # out of it to work out the assembled size.
    t.set_jobs([{"id": "j1", "name": "j", "type": job_type, "state": state,
                 "blend": BLEND, "script": config.DEFAULT_SCRIPTS[job_type],
                 "config": cfg}])
    app.processEvents()
    return t


def cell(t, key):
    return t.item(0, COL[key])


print("\n=== 1. the dropdown ===")
t = row(MULTI, {"MODE": "animation"})
sc = cell(t, "scene")
check("a multi-scene .blend shows the active scene", sc.text() == "Alpha",
      f"got {sc.text()!r}")
check("...and the cell is editable", bool(sc.flags() & QtCore.Qt.ItemIsEditable))
check("...offering every scene in the file",
      sc.data(QtCore.Qt.UserRole + 2)[3] == ["Alpha", "Beta"],
      f"got {sc.data(QtCore.Qt.UserRole + 2)[3]}")

t = row(SINGLE, {"MODE": "animation"})
sc = cell(t, "scene")
check("a single-scene .blend is greyed out, as asked",
      not (sc.flags() & QtCore.Qt.ItemIsEditable))
check("...and says why", "only one scene" in (sc.toolTip() or ""), sc.toolTip())
check("...while still naming the scene", sc.text() == "Scene", sc.text())

print("\n=== 2. the whole row follows the chosen scene ===")
# This is the point of it: Beta has a different camera, shape and sample count,
# and showing Alpha's numbers next to a job that will render Beta would be
# worse than showing nothing.
t = row(MULTI, {"MODE": "animation", "SCENE": "Beta"})
check("the Scene cell shows the choice", cell(t, "scene").text() == "Beta")
check("the camera follows it", cell(t, "camera").text() == "CamBeta",
      f"got {cell(t, 'camera').text()!r}")
check("the resolution follows it", cell(t, "resolution").text() == "1080×1350",
      f"got {cell(t, 'resolution').text()!r}")
check("the samples follow it", cell(t, "samples").text() == "4096",
      f"got {cell(t, 'samples').text()!r}")
check("the format follows it", cell(t, "format").text() == "JPEG",
      f"got {cell(t, 'format').text()!r}")

t = row(MULTI, {"MODE": "animation", "SCENE": "Beta"})
cams = cell(t, "camera").data(QtCore.Qt.UserRole + 2)[3]
check("the camera dropdown offers only THAT scene's cameras",
      cams == ["CamBeta", "CamB2"],
      "a camera living in another scene cannot be rendered from this one, so "
      f"offering it would be an entry that can only fail.  got {cams}")

print("\n=== 3. a tiled job picks its scene too ===")
t = row(MULTI, {"SCENE": "Beta", "LONG_EDGE": 120000, "TILE_LONG": 4096},
        job_type="tiled")
check("the scene cell works for tiled as well",
      cell(t, "scene").text() == "Beta")
check("...and the grid is computed from THAT scene's aspect",
      cell(t, "resolution").text() == "96000×120000",
      "Beta is 1080x1350 portrait, so the long edge lands on height.  "
      f"got {cell(t, 'resolution').text()!r}")

print("\n=== 4. it cannot be changed once it would do damage ===")
t = row(MULTI, {"MODE": "animation"}, state="running")
check("a running job's scene is locked",
      not (cell(t, "scene").flags() & QtCore.Qt.ItemIsEditable),
      "switching scene mid-render would mix two different images in one folder")

print("\n=== 5. the plumbing lines up ===")
import server                                          # noqa: E402
check("the server allows SCENE on a tiled job",
      "SCENE" in server.Handler.EDITABLE["tiled"])
check("...and on a classic job",
      "SCENE" in server.Handler.EDITABLE["classic"])

for label, path in (("tiled", config.DEFAULT_TILED_SCRIPT),
                    ("classic", config.DEFAULT_CLASSIC_SCRIPT)):
    src = open(path, encoding="utf-8").read()
    check(f"the default {label} script accepts SCENE",
          '"SCENE",' in src, os.path.basename(path))
    check(f"...and actually switches to it",
          "def _apply_scene" in src and "win.scene = target" in src)
    check(f"...before anything reads the scene",
          src.index("_apply_scene()") < src.index("bpy.context.scene\n")
          if "bpy.context.scene\n" in src else True)

import paths                                           # noqa: E402
check("a job pinned to an OLD snapshot rejects SCENE automatically",
      "SCENE" not in (paths.script_overridable(
          os.path.join(APP, "frozen", "render_tiles_unlimited_2026-07-26.py"))
          or set()),
      "the live 120k job stays on that snapshot, and the edit guard narrows "
      "what it will accept to what its own script understands")

print("\n=== 6. OUTPUT_DIR is a Blender path, not simply a folder ===")
# Worth pinning down, because this looked like a bug and was not. To Blender a
# path with no trailing separator is a FILENAME PREFIX, so an OUTPUT_DIR of
# ".../still/shot_" writes shot_0004.png — deliberate, and test_classic relies
# on it. "Fixing" it by appending a separator in the script turned shot_ into a
# directory and broke four real-render checks. The separator is the business of
# whoever sets the value.
src = open(config.DEFAULT_CLASSIC_SCRIPT, encoding="utf-8").read()
check("the script passes OUTPUT_DIR through untouched",
      "r.filepath = OUTPUT_DIR" in src,
      "so a prefix stays a prefix and a folder stays a folder")

os.remove(BLEND)
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
