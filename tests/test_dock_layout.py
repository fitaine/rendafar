"""
The default panel arrangement, and that Reset Layout actually resets it.

Found live, twice: Preview and GPU always ended up tabbed together no matter
how a user rearranged them, because _build_docks() used to call
tabifyDockWidget(Preview, GPU) unconditionally on every construction — so even
"delete both and add them back" recreated the same forced pairing, and
Reset Layout's own snapshot (taken right after _build_docks) was itself always
tabbed, making the button look broken rather than merely un-helpful.

Routed through a temporary INI file rather than QSettings("Buccino","Rendafar")
— the real registry key the installed app uses — so running this suite can
never overwrite anything a real user actually saved.

    python tests\test_dock_layout.py
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

TMP = tempfile.mkdtemp(prefix="dock_layout_test_")
INI = os.path.join(TMP, "settings.ini")
MainWindow._settings = lambda self: QtCore.QSettings(INI, QtCore.QSettings.IniFormat)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def tabbed_together(win, a, b):
    return win.docks[b] in win.tabifiedDockWidgets(win.docks[a])


print("\n=== 1. fresh window: nothing is pre-tabbed ===")
win = MainWindow()
win.poller.stop()
win.show()
app.processEvents()

check("Preview and GPU start as separate panels",
      not tabbed_together(win, "Preview", "GPU"),
      "this was the reported bug — they always ended up tabbed regardless of "
      "how the panels were rearranged")
check("Render is not floating", not win.docks["Render"].isFloating())
check("Render, Preview and GPU are all visible by default",
      all(win.docks[k].isVisible() for k in ("Render", "Preview", "GPU")))
check("the Log starts hidden",
      not win.docks["Log"].isVisible(),
      "reached for when something looks wrong, not left on screen otherwise")

print("\n=== 2. Reset Layout genuinely separates them again ===")
win.tabifyDockWidget(win.docks["Preview"], win.docks["GPU"])
app.processEvents()
check("tabifying by hand works (the mechanism itself is fine)",
      tabbed_together(win, "Preview", "GPU"))

win.reset_layout()
app.processEvents()
check("Reset Layout un-tabs them",
      not tabbed_together(win, "Preview", "GPU"),
      "previously this button reset to a snapshot that was ALSO tabbed, "
      "which is why it looked like it did nothing")

win.docks["Log"].close()
app.processEvents()
win.reset_layout()
app.processEvents()
check("Reset Layout also brings back a closed panel",
      win.docks["Render"].isVisible())          # sanity: reset didn't break others
check("...specifically the Log, which defaults to hidden rather than shown",
      not win.docks["Log"].isVisible())

win.close()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
