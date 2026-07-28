"""
The single Start button: what it does, and what it says it will do.

Start and Resume used to be separate buttons in separate panels, and Resume
did not start anything — it only moved a job back to the front of the queue as
'queued'. So continuing a stopped render meant: select the row, press Resume
(in the Jobs panel), then press Start queue (in the Render panel). Worse, on a
fresh launch with one stopped job and nothing selected, BOTH were greyed out
and the header read "No renders queued", which looked like a broken app rather
than "click the row first".

They are one button now, and every press routes through _start_target() so the
label can never promise something different from what the click does.

    python tests\test_start_button.py
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

INI = os.path.join(tempfile.mkdtemp(prefix="start_btn_"), "settings.ini")
MainWindow._settings = lambda self: QtCore.QSettings(INI, QtCore.QSettings.IniFormat)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


win = MainWindow()
win.poller.stop()
win.show()
app.processEvents()


def scenario(state, selected_id=None, busy=False, next_id=None, job_id=None):
    """Drive the window from a fake server status, the way the poller would."""
    win._last = {
        "busy": busy,
        "next_id": next_id,
        "job_id": job_id,
        "job": None,
        "queue": state,
    }
    win._selected_job = lambda: next(
        (j for j in state if j["id"] == selected_id), None)
    return win._start_target()


STOPPED = [{"id": "job_0001", "name": "Gervasutti", "state": "stopped"}]
QUEUED = [{"id": "job_0002", "name": "Next one", "state": "queued"}]
DONE = [{"id": "job_0003", "name": "Finished", "state": "done"}]

print("\n=== 1. the dead end that started this ===")
jid, label, on = scenario(STOPPED, selected_id=None, next_id=None)
check("nothing selected, one stopped job: Start is still disabled",
      not on,
      "unchanged — but it is no longer a dead end, because the header now "
      "says a render is stopped instead of 'No renders queued'")

jid, label, on = scenario(STOPPED, selected_id="job_0001", next_id=None)
check("selecting the stopped job enables Start", on)
check("...and it reads 'Resume render'", label == "Resume render", f"got {label!r}")
check("...and it targets that specific job", jid == "job_0001", f"got {jid!r}")

print("\n=== 2. one press, not two ===")
calls = []
import desktop.main_window as mw
real_act = win._act
win._act = lambda fn, *a: calls.append((getattr(fn, "__name__", str(fn)), a))

scenario(STOPPED, selected_id="job_0001", next_id=None)
win.do_start()
check("pressing Start on a stopped job issues exactly one call",
      len(calls) == 1, f"got {calls}")
check("...and it is start(job_id), which re-queues AND runs it",
      calls and calls[0][0] == "start" and calls[0][1] == ("job_0001",),
      f"got {calls}")
check("resume() is not called at all any more",
      all(c[0] != "resume" for c in calls),
      "resume-then-start was the two-step dance this replaced")

print("\n=== 3. a disabled button does nothing when triggered ===")
calls.clear()
scenario(STOPPED, selected_id=None, next_id=None)
win.do_start()
check("do_start() on a disabled state is a no-op",
      calls == [],
      "the menu action shares this code path, and shortcuts can fire it "
      "even while the button is greyed")

print("\n=== 4. plain queue behaviour is unchanged ===")
calls.clear()
jid, label, on = scenario(QUEUED, selected_id=None, next_id="job_0002")
check("with a queued job and no selection, Start is enabled", on)
check("...and reads 'Start queue'", label == "Start queue", f"got {label!r}")
check("...and targets the queue, not a specific job", jid is None, f"got {jid!r}")
win.do_start()
check("...and calls start() with no job id",
      calls and calls[0][1] == (None,), f"got {calls}")

jid, label, on = scenario(QUEUED, selected_id="job_0002", next_id="job_0002")
check("selecting a queued job targets it explicitly",
      jid == "job_0002" and label == "Start selected", f"got {jid!r} {label!r}")

print("\n=== 5. guards ===")
jid, label, on = scenario(STOPPED, selected_id="job_0001", busy=True,
                          next_id=None, job_id="job_0001")
check("while a render is running, Start is disabled",
      not on, "Stop is the button that applies then")

jid, label, on = scenario(DONE, selected_id="job_0003", next_id=None)
check("selecting a DONE job does not offer to restart it",
      not on and jid is None,
      "re-rendering a finished job costs hours — not a single unconfirmed click")

print("\n=== 6. the label always matches the action ===")
for st, sel, nid in (("stopped", "job_0001", None),
                     ("queued", "job_0002", "job_0002")):
    data = [{"id": sel, "name": "x", "state": st}]
    jid, label, on = scenario(data, selected_id=sel, next_id=nid)
    calls.clear()
    win.do_start()
    check(f"{label!r} actually calls start({jid!r})",
          calls and calls[0][1] == (jid,), f"got {calls}")

print("\n=== 7. the phone UI does not keep the old two-step ===")
# The web UI had the same trap in a worse shape: a per-job "Resume" that only
# re-queued, after which a separate "Start queue" button appeared at the top
# to be tapped as well.
html = open(os.path.join(APP, "static", "index.html"), encoding="utf-8").read()
check("the phone's Resume button calls start, not resume",
      "act('start','${j.id}')" in html and "act('resume'" not in html,
      "one tap resumes a stopped render, same as on the desktop")
check("the queue button says 'Start queue'",
      "Start queue</button>" in html and "Start next job</button>" not in html,
      "matched against the button markup, not the whole file — the comment "
      "above it legitimately mentions the old wording")

win._act = real_act
win.close()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
