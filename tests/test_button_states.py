"""
A disabled button has to LOOK disabled.

Reported live: "Stop button needs to be greyed out when things are already
stopped, or on idle. If not it is misleading (is that running?)".

The enablement was already right — _sync_buttons() gates Stop on `busy`. The
bug was purely visual: QPushButton#danger set a red text and border, and an id
selector outranks QPushButton:disabled, so the greyed-out Stop was painted
exactly like the live one. #primary already carried its own :disabled rule;
#danger was the one missing it.

Checked by rendering the buttons and comparing pixels rather than by matching
strings in the stylesheet, because the failure was that two renderings were
identical — which no amount of grepping the CSS would have caught.

    python tests\test_button_states.py
"""

import os
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

from desktop import theme

theme.apply(app)

from desktop.main_window import MainWindow

MainWindow._settings = lambda self: QtCore.QSettings(
    os.path.join(tempfile.mkdtemp(prefix="btnstate_"), "s.ini"),
    QtCore.QSettings.IniFormat)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def painted(object_name, enabled, text="Stop render"):
    """The actual pixels Qt draws for this button in this state."""
    b = QtWidgets.QPushButton(text)
    if object_name:
        b.setObjectName(object_name)
    b.setEnabled(enabled)
    b.resize(140, 34)
    b.show()
    app.processEvents()
    img = b.grab().toImage()
    b.hide()
    return bytes(img.constBits())


print("\n=== 1. a disabled button is drawn differently ===")
for name in ("danger", "primary", None):
    on = painted(name, True)
    off = painted(name, False)
    check(f"#{name or 'plain'}: enabled and disabled do not render identically",
          on != off,
          "this is exactly what failed for #danger — correctly greyed out in "
          "code, pixel-identical on screen" if name == "danger" else "")

print("\n=== 2. Stop follows whether anything is actually rendering ===")
win = MainWindow()
win.poller.stop()
win.show()
app.processEvents()

RUNNING = [{"id": "job_0001", "name": "Bure", "state": "running"}]
STOPPED = [{"id": "job_0001", "name": "Bure", "state": "stopped"}]


def state(queue, busy, job_id=None):
    win._last = {"busy": busy, "queue": queue, "next_id": None,
                 "job_id": job_id, "job": queue[0] if queue else None}
    win._selected_job = lambda: None
    win._sync_buttons()
    app.processEvents()


state(RUNNING, busy=True, job_id="job_0001")
check("while a render is going, Stop is enabled", win.btn_stop.isEnabled())
check("...and so is the menu entry", win.act_stop.isEnabled())

state(STOPPED, busy=False)
check("with the render stopped, Stop is disabled",
      not win.btn_stop.isEnabled(),
      "the misleading case: a live-looking Stop makes you ask whether it is "
      "still rendering")
check("...and so is the menu entry", not win.act_stop.isEnabled())

state([], busy=False)
check("with nothing at all, Stop is disabled", not win.btn_stop.isEnabled())

print("\n=== 3. the real Stop button greys, in place ===")
state(RUNNING, busy=True, job_id="job_0001")
live = bytes(win.btn_stop.grab().toImage().constBits())
state(STOPPED, busy=False)
dead = bytes(win.btn_stop.grab().toImage().constBits())
check("the same button renders differently once disabled",
      live != dead,
      "grabbed from the actual window, not a stand-in widget")

print("\n=== 4. the phone never shows a Stop it cannot mean ===")
html = open(os.path.join(APP, "static", "index.html"), encoding="utf-8").read()
i = html.index("function actions(s)")
block = html[i:i + 400]
check("the phone only renders Stop when busy",
      "if (s.busy)" in block and "act('stop')" in block,
      "it omits the button entirely rather than greying it, which cannot be "
      "misread either way")
check("...and disabled buttons are dimmed by opacity, which .danger cannot "
      "override", "button:disabled{opacity:.35" in html.replace(" ", ""),
      "opacity is not among the properties .danger sets, so the web styling "
      "never had the specificity problem the desktop one did")

win.close()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
