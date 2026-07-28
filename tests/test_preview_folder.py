"""
The Open folder button in the Preview panel.

The images are already on screen there, so getting to them on disk should not
mean going back up to the File menu.

It is greyed rather than always clickable: a classic render has no output_dir
until it has started, because the path comes from the .blend, and a button that
opens a warning box is worse than one that visibly cannot be pressed yet.

    python tests\test_preview_folder.py
"""

import os
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

from desktop.main_window import MainWindow

INI = os.path.join(tempfile.mkdtemp(prefix="prev_folder_"), "settings.ini")
MainWindow._settings = lambda self: QtCore.QSettings(INI, QtCore.QSettings.IniFormat)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


win = MainWindow()
win.poller.stop()
win.show()
app.processEvents()

print("\n=== 1. the button exists, next to Show preview ===")
btn = win.preview.btn_folder
check("the Preview panel has an Open folder button", btn is not None)
check("it is labelled plainly", btn.text() == "Open folder", f"got {btn.text()!r}")

# The header row is the first thing added to the panel's layout.
lay = win.preview.lay.itemAt(0).layout()
order = [lay.itemAt(i).widget() for i in range(lay.count())]
order = [w for w in order if w is not None]
check("it sits immediately after the Show preview checkbox",
      order[:2] == [win.preview.enabled, btn],
      f"got {[type(w).__name__ + ':' + (w.text() if hasattr(w,'text') else '') for w in order]}")

print("\n=== 2. it opens the folder of the job in view ===")
opened = []
win._open = lambda target: opened.append(target)

tmp_out = tempfile.mkdtemp(prefix="fake_out_")
job = {"id": "job_0001", "name": "Anim", "state": "running",
       "output_dir": tmp_out, "blend": os.path.join(tmp_out, "x.blend")}
win._last = {"busy": True, "job": job, "queue": [job], "job_id": "job_0001"}
win._selected_job = lambda: None          # nothing selected: falls back to running

btn.click()
check("clicking it opens the running job's output folder",
      opened == [tmp_out], f"got {opened}")

print("\n=== 3. it follows the selection, like the menu action does ===")
opened.clear()
other = tempfile.mkdtemp(prefix="fake_sel_")
sel = {"id": "job_0002", "name": "Other", "state": "queued", "output_dir": other}
win._selected_job = lambda: sel
btn.click()
check("with a job selected, it opens THAT job's folder",
      opened == [other],
      "otherwise the button and the panel below it would disagree about "
      f"which render you are looking at.  got {opened}")

print("\n=== 4. greyed when there is nothing to open ===")
win._selected_job = lambda: None
win._last = {"busy": False, "job": None, "queue": []}
win._sync_buttons()
check("no job at all: the button is disabled", not btn.isEnabled())
check("...and so is the File menu entry", not win.act_open_out_dir.isEnabled())

started_classic = {"id": "job_0003", "name": "Classic", "state": "queued",
                   "blend": "C:/x/y.blend"}          # no output_dir yet
win._last = {"busy": False, "job": started_classic, "queue": [started_classic]}
win._sync_buttons()
check("a classic render with no output_dir yet: still disabled",
      not btn.isEnabled(),
      "the path comes from the .blend and is not known until it starts")
check("...but its blend folder IS openable",
      win.act_open_blend_dir.isEnabled())

win._last = {"busy": False, "job": job, "queue": [job]}
win._sync_buttons()
check("once there is an output_dir, the button enables", btn.isEnabled())

win.close()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
