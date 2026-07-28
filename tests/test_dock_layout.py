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

print("\n=== 2b. the column divider is ONE drag handle, not two ===")
# Reported live: the vertical divider between the Jobs column and the right
# hand column showed two draggable lines side by side. The central widget was
# sized to 0x0 but still present, so it sat between the left and right dock
# areas and Qt drew a separator on each side of it — two handles with a dead
# sliver between them. Hiding the central widget makes the areas meet directly.
win.resize(1400, 850)
win.show()
app.processEvents()
QtCore.QThread.msleep(100)
app.processEvents()

sep = app.style().pixelMetric(QtWidgets.QStyle.PM_DockWidgetSeparatorExtent)
jg = win.docks["Jobs"].geometry()
rg = win.docks["Render"].geometry()
divider = rg.left() - jg.right() - 1

check("the central widget is hidden, not merely zero-sized",
      not win.centralWidget().isVisible(),
      "a 0x0 visible central widget is what produced the double separator")
check(f"the divider is one separator wide ({divider}px, separator is {sep}px)",
      divider <= sep + 1,
      f"two handles measured {2 * sep}px here before the fix")

print("\n=== 3. a tab can be dragged back OUT of a tab group ===")
# This one is driven by a REAL press/move/release on the QTabBar rather than
# by setFloating(True). That distinction is the whole point: setFloating()
# bypasses the drag gesture entirely, so the earlier tests here passed for
# months while the actual reported bug — "can drag a panel in, can never get
# it out" — was fully present. Only a synthetic drag can catch it.
#
# The cause was a missing QMainWindow.GroupedDragging dock option. Qt gates
# ALL of QMainWindowTabBar's drag-out-of-the-tab-bar handling behind that
# flag; without it a QTabBar can only reorder its own tabs.
from PySide6 import QtTest

check("GroupedDragging is enabled",
      bool(win.dockOptions() & QtWidgets.QMainWindow.GroupedDragging),
      "this is the actual root cause — delete the flag and dragging a tab out "
      "silently stops working, so this assertion is the real regression guard")

# The synthetic drag below needs its OWN window. Qt recycles tab bars between
# tabify/reset cycles, and a window that has already been through section 2's
# reset_layout rounds will not complete a QTest-driven drag even though the
# same drag succeeds every time on a fresh window. That is a property of
# QTest, not of the app — so reusing `win` here would make this test fail on
# a perfectly working build.
drag_win = MainWindow()
drag_win.poller.stop()
drag_win.resize(1200, 800)
drag_win.show()
app.processEvents()
drag_win.tabifyDockWidget(drag_win.docks["Preview"], drag_win.docks["GPU"])
app.processEvents()

bar = idx = None
for b in drag_win.findChildren(QtWidgets.QTabBar):
    for i in range(b.count()):
        if "GPU" in b.tabText(i).upper():
            bar, idx = b, i
            break
    if bar:
        break

check("Qt built a real QTabBar for the group", bar is not None)

if bar is not None:
    start = bar.tabRect(idx).center()

    # Release well OUTSIDE the main window. Dropping a few hundred pixels below
    # the tab bar looks like "dragged out" but is still inside the right dock
    # area, so Qt correctly re-docks it there and the panel never floats —
    # which reads as a failure even when detaching works perfectly.
    outside_global = QtCore.QPoint(drag_win.x() + drag_win.width() + 400,
                                   drag_win.y() + 250)
    end = bar.mapFromGlobal(outside_global)

    QtTest.QTest.mousePress(bar, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
    app.processEvents()
    # Qt only starts the detach once the cursor leaves the tab bar's rect by
    # roughly startDragDistance * 3, so a single jump is not enough — it needs
    # intermediate moves for the drag state machine to advance. AnimatedDocks
    # also settles asynchronously, so each step needs the event loop to breathe.
    for frac in (0.1, 0.3, 0.6, 1.0):
        step = start + (end - start) * frac
        QtTest.QTest.mouseMove(bar, step)
        app.processEvents()
        QtCore.QThread.msleep(30)
        app.processEvents()
    QtTest.QTest.mouseRelease(bar, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
    app.processEvents()
    QtCore.QThread.msleep(200)
    app.processEvents()

    check("dragging the GPU tab out un-tabs it",
          drag_win.docks["GPU"] not in
              drag_win.tabifiedDockWidgets(drag_win.docks["Preview"]),
          "the reported bug: panels could be dragged together into tabs but "
          "never separated again")
    check("...and it ends up floating on its own",
          drag_win.docks["GPU"].isFloating())

drag_win.close()
app.processEvents()

print("\n=== 4. layouts still save and restore with grouped dragging on ===")
# GroupedDragging makes Qt use QDockWidgetGroupWindow for floating groups,
# which is a different structure to serialise — worth proving saveState and
# restoreState survive a round trip rather than assuming they do.
win.reset_layout()
app.processEvents()
win.tabifyDockWidget(win.docks["Preview"], win.docks["GPU"])
app.processEvents()
blob = win.saveState()
check("saveState() returns a non-empty layout", not blob.isEmpty())

win.reset_layout()
app.processEvents()
check("reset separated them again", not tabbed_together(win, "Preview", "GPU"))

check("restoreState() accepts the saved layout", win.restoreState(blob))
app.processEvents()
check("...and the tabbed arrangement comes back",
      tabbed_together(win, "Preview", "GPU"))

win.reset_layout()
app.processEvents()

win.close()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
