"""
Queueing several cameras from one .blend in a single go.

Asked for after using the scene selector: rather than adding a file, then
duplicating the job and changing the camera and scene by hand for each one, the
New render dialog should let you tick "camera 1 of scene 1, camera 2 of scene 2,
not camera 3" and produce those jobs directly.

The trap this has to avoid is silent: several stills of one .blend render the
same frame number to the same path and overwrite each other, so the last one
wins and the rest of the work is gone. Each job therefore gets the camera's
name in its output.

Verified with real renders before these were written: two picked cameras of a
two-scene file produced CamAlpha_0001.png at 64x64 and CamBeta_0001.png at
32x48 — the right camera, the right scene's resolution, two separate files.

    python tests\test_multi_camera.py
"""

import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

# _create_many ends by reporting what it queued, and a modal box with nobody to
# click it hangs the run forever. Stubbed rather than avoided, so the code under
# test is the real code path.
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)

from desktop import api, new_render
from desktop.new_render import NewRenderDialog

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


BLEND = os.path.join(APP, "tests", "_multicam.blend")
open(BLEND, "wb").write(b"x")

INFO = {
    "scene": "Alpha", "active_scene": "Alpha",
    "camera": "CamAlpha", "cameras": ["CamAlpha", "Spare"],
    "filepath": r"D:\renders\shot_",
    "scenes": [
        {"scene": "Alpha", "camera": "CamAlpha",
         "cameras": ["CamAlpha", "Spare"], "filepath": r"D:\renders\alpha\\"},
        {"scene": "Beta", "camera": "CamBeta", "cameras": ["CamBeta"],
         "filepath": r"D:\renders\beta_out"},
    ],
}

print("\n=== 1. where each camera writes ===")
# The rule lives in paths.unique_classic_output now, so the dialog, the server
# and the Filename column give one answer instead of three copies of it.
import paths                                     # noqa: E402
B = "D:" + chr(92) + "r" + chr(92)
u = paths.unique_classic_output
check("a folder gets the camera folded into the filename",
      u(B, {"CAMERA": "CamAlpha"}, "j") == B + "CamAlpha_",
      u(B, {"CAMERA": "CamAlpha"}, "j"))
check("an existing prefix is extended, not replaced",
      u(B + "shot_", {"CAMERA": "CamB"}, "j") == B + "shot_CamB_",
      u(B + "shot_", {"CAMERA": "CamB"}, "j"))
check("awkward camera names are made safe",
      u(B, {"CAMERA": "Camera 6.5x6.3 ratio"}, "j") == B + "Camera_6.5x6.3_ratio_",
      u(B, {"CAMERA": "Camera 6.5x6.3 ratio"}, "j"))
check("an unknown output leaves the .blend in charge", u("", {}, "j") == "")
check("two cameras never land on the same path",
      u(B, {"CAMERA": "A"}, "j") != u(B, {"CAMERA": "B"}, "j"),
      "this is the whole point — otherwise they overwrite each other")
check("a duplicate is broken by a counter",
      u(B, {"CAMERA": "A"}, "j", {B + "A_"}) == B + "A_2_",
      u(B, {"CAMERA": "A"}, "j", {B + "A_"}))

print("\n=== 1b. the filename is shown, not left to be discovered ===")
check("a bare folder gives a frame-numbered name",
      paths.preview_filename("", "PNG", 1) == "0001.png",
      "identical for every job sharing that folder — which is the trap")
check("a prefix makes it distinct",
      paths.preview_filename("Camera_main_", "PNG", 1) == "Camera_main_0001.png")
check("the extension follows the format",
      paths.preview_filename("a_", "JPEG", 4) == "a_0004.jpg",
      paths.preview_filename("a_", "JPEG", 4))
check("folder and filename split apart",
      paths.split_classic_output(B + "Cam_") == (B, "Cam_"),
      str(paths.split_classic_output(B + "Cam_")))
check("...and rejoin", paths.join_classic_output("D:" + chr(92) + "r", "Cam_")
      == B + "Cam_")


print("\n=== 2. the picker ===")
api.blend_info = lambda path, refresh=False: INFO
created = []
api.new_job = lambda body: (created.append(body),
                            {"job": {"name": body["name"], "id": "j"}})[1]

d = NewRenderDialog(blend=BLEND)
d.kind_group.button(2).setChecked(True)          # Still
d._kind_chosen(2)
for _ in range(100):
    app.processEvents()
    QtCore.QThread.msleep(10)
    if d.cams.topLevelItemCount():
        break
app.processEvents()

check("every scene is listed", d.cams.topLevelItemCount() == 2,
      f"got {d.cams.topLevelItemCount()}")
kids = [d.cams.topLevelItem(i).child(j).text(0)
        for i in range(d.cams.topLevelItemCount())
        for j in range(d.cams.topLevelItem(i).childCount())]
check("with their own cameras under them",
      kids == ["CamAlpha", "Spare", "CamBeta"], str(kids))
check("only the active scene's camera starts ticked",
      d.selected_cameras() == [("Alpha", "CamAlpha")],
      "everything else is a decision, not a default — one Add should not "
      f"quietly queue three renders.  got {d.selected_cameras()}")


def tick(name, on=True):
    for i in range(d.cams.topLevelItemCount()):
        t = d.cams.topLevelItem(i)
        for j in range(t.childCount()):
            if t.child(j).text(0) == name:
                t.child(j).setCheckState(
                    0, QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)
    app.processEvents()


print("\n=== 3. Add is refused with nothing ticked ===")
tick("CamAlpha", False)
check("untick everything and Add goes dead", not d.ok.isEnabled())
check("...and says why", "camera" in d.ok.toolTip().lower(), d.ok.toolTip())

print("\n=== 4. one job per ticked camera ===")
tick("CamAlpha")
tick("CamBeta")
check("two ticked, two selected",
      d.selected_cameras() == [("Alpha", "CamAlpha"), ("Beta", "CamBeta")])
check("the note says how many jobs that is",
      "2 jobs" in d.cams_note.text(), d.cams_note.text().replace("\n", " | "))

created.clear()
d._create_many(d.selected_cameras())
check("two jobs were created", len(created) == 2, f"got {len(created)}")
check("each carries its own scene and camera",
      [(b.get("scene"), b.get("camera")) for b in created]
      == [("Alpha", "CamAlpha"), ("Beta", "CamBeta")],
      str([(b.get("scene"), b.get("camera")) for b in created]))
outs = [b.get("output_dir") for b in created]
check("...and writes somewhere of its own", len(set(outs)) == 2, str(outs))
check("the names say which camera",
      all("CamAlpha" in created[0]["name"] and "CamBeta" in created[1]["name"]
          for _ in [0]),
      str([b["name"] for b in created]))
check("...naming the scene too, since they differ",
      "Alpha/" in created[0]["name"], created[0]["name"])

print("\n=== 5. a single ticked camera still counts ===")
# The one-job path goes through payload(), not _create_many, so the tick has to
# be honoured there as well or picking one camera would render the default.
tick("CamBeta", False)
tick("CamAlpha", False)
tick("Spare")
body = d.payload()
check("payload carries the ticked camera",
      body.get("camera") == "Spare", str(body.get("camera")))
check("...and its scene", body.get("scene") == "Alpha", str(body.get("scene")))

print("\n=== 6. the server accepts those fields ===")
import inspect                                   # noqa: E402
import server                                    # noqa: E402
src = inspect.getsource(server.Handler._new_job)
for key, cfgkey in (("camera", "CAMERA"), ("scene", "SCENE"),
                    ("output_dir", "OUTPUT_DIR")):
    check(f"/api/new maps {key} -> {cfgkey}",
          f'body.get("{key}")' in src and f'cfg["{cfgkey}"]' in src)

os.remove(BLEND)
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
