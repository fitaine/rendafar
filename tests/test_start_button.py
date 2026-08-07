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
check("nothing selected, one stopped job: Start just works",
      on and jid == "job_0001",
      "this used to be disabled until a row was selected, which made the "
      "commonest action in the app a two-step hunt ending at a dead button")

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
# A finished job is the disabled case now that a stopped one starts on its own.
scenario(DONE, selected_id="job_0003", next_id=None)
win.do_start()
check("do_start() on a disabled state is a no-op",
      calls == [],
      "the menu action shares this code path, and Ctrl+R can fire it even "
      "while the button is greyed")

print("\n=== 4. plain queue behaviour is unchanged ===")
calls.clear()
jid, label, on = scenario(QUEUED, selected_id=None, next_id="job_0002")
check("with a queued job and no selection, Start is enabled", on)
check("...and reads 'Start queue'", label == "Start queue", f"got {label!r}")
check("...and targets the queue, not a specific job", jid is None, f"got {jid!r}")
win.do_start()
check("...and calls start() with no job id",
      calls and calls[0][1] == (None,), f"got {calls}")

# Queue order beats selection. Selecting a row is how you inspect a job, so
# letting it re-point Start meant highlighting a row to read its settings
# quietly jumped that job ahead of everything above it. The arrows are how you
# say "this one first" — that is deliberate and visible, a selection is not.
jid, label, on = scenario(QUEUED, selected_id="job_0002", next_id="job_0002")
check("selecting a queued job does NOT re-target Start",
      jid is None and label == "Start queue", f"got {jid!r} {label!r}")

jid, label, on = scenario(QUEUED, selected_id="job_0009", next_id="job_0002")
check("...even when the selected job is further down the queue",
      jid is None and label == "Start queue",
      f"selecting job_0009 must not jump it ahead of job_0002. got {jid!r} {label!r}")

print("\n=== 4b. a stopped job stays reachable ===")
# The cost of queue-order-wins: a stopped job is NOT in the queue (next_queued
# skips it), so no amount of moving it up reaches it, and Start now offers the
# queue instead. Without an explicit way out it would be unstartable whenever
# anything at all is queued — which is exactly the live situation: one stopped
# animation sitting behind two queued 120k renders.
BOTH = [{"id": "job_0001", "name": "Gervasutti", "state": "stopped"},
        {"id": "job_0002", "name": "Bure",       "state": "queued"}]

jid, label, on = scenario(BOTH, selected_id="job_0001", next_id="job_0002")
check("a stopped job selected with a full queue: Start runs the QUEUE",
      jid is None and label == "Start queue", f"got {jid!r} {label!r}")

win._sync_buttons()
check("...and Render > Resume selected is enabled instead",
      win.act_resume.isEnabled(),
      "otherwise the stopped job could never be started at all")

calls.clear()
win.do_resume()
check("...which starts that specific job, in one call",
      len(calls) == 1 and calls[0][0] == "start"
      and calls[0][1] == ("job_0001",), f"got {calls}")

# And when the queue empties, the button itself offers it again.
jid, label, on = scenario(BOTH, selected_id="job_0001", next_id=None)
check("with nothing queued, Start reads 'Resume render' again",
      jid == "job_0001" and label == "Resume render", f"got {jid!r} {label!r}")

print("\n=== 4c. Resume selected refuses what it should ===")
calls.clear()
scenario(QUEUED, selected_id="job_0002", next_id="job_0002")
win._sync_buttons()
check("a QUEUED job is not resumable", not win.act_resume.isEnabled())
win.do_resume()
check("...and triggering it anyway does nothing", calls == [], f"got {calls}")

calls.clear()
scenario(DONE, selected_id="job_0003", next_id=None)
win._sync_buttons()
check("a DONE job is not resumable either", not win.act_resume.isEnabled(),
      "re-rendering hours of finished tiles is not a one-click action")
win.do_resume()
check("...and triggering it does nothing", calls == [], f"got {calls}")

calls.clear()
scenario(BOTH, selected_id="job_0001", busy=True, next_id=None,
         job_id="job_0002")
win._sync_buttons()
check("nothing is resumable while a render is running",
      not win.act_resume.isEnabled())

print("\n=== 4d. Queue: set it waiting, do not start it ===")
# The verb that was missing. A stopped job is parked; start_next() only picks
# jobs whose state is 'queued', which is why two finished renders were
# followed by nothing.
calls.clear()
scenario(BOTH, selected_id="job_0001", busy=True, next_id="job_0002",
         job_id="job_0002")
win._sync_buttons()
check("Queue is enabled for a stopped job EVEN WHILE rendering",
      win.btn_queue.isEnabled() and win.act_queue.isEnabled(),
      "lining up what runs next mid-render is the main reason it exists")
check("...while Resume is not, since the GPU is busy",
      not win.act_resume.isEnabled())

win.do_enqueue()
check("...and it calls enqueue, not start",
      len(calls) == 1 and calls[0][0] == "enqueue"
      and calls[0][1] == ("job_0001",), f"got {calls}")

calls.clear()
scenario(QUEUED, selected_id="job_0002", next_id="job_0002")
win._sync_buttons()
check("an already-queued job cannot be queued again",
      not win.btn_queue.isEnabled())
win.do_enqueue()
check("...and triggering it does nothing", calls == [], f"got {calls}")

calls.clear()
scenario(DONE, selected_id="job_0003", next_id=None)
win._sync_buttons()
check("a done job cannot be queued", not win.btn_queue.isEnabled(),
      "re-rendering hours of finished tiles is not a one-click action")
win.do_enqueue()
check("...and triggering it does nothing", calls == [], f"got {calls}")

print("\n=== 4e. one click, no selection ===")
# Start used to need a row selected before it would touch a stopped job, so it
# sat greyed out with an obviously resumable render on screen and "start"
# became a two-step hunt.
calls.clear()
jid, label, on = scenario(STOPPED, selected_id=None, next_id=None)
check("a stopped job with NOTHING selected enables Start", on,
      "this is the one-click case")
check("...targeting it without being asked", jid == "job_0001", f"got {jid!r}")
win.do_start()
check("...and one press starts it",
      calls == [("start", ("job_0001",))], f"got {calls}")

# Queue order still decides which one, not the selection.
TWO = [{"id": "job_0001", "name": "first",  "state": "stopped"},
       {"id": "job_0002", "name": "second", "state": "stopped"}]
jid, label, on = scenario(TWO, selected_id="job_0002", next_id=None)
check("with two stopped jobs it takes the FIRST in queue order",
      jid == "job_0001",
      f"selecting the second must not re-point it.  got {jid!r}")

# A genuinely queued job still outranks a stopped one.
MIX = [{"id": "job_0001", "name": "stopped one", "state": "stopped"},
       {"id": "job_0002", "name": "queued one",  "state": "queued"}]
jid, label, on = scenario(MIX, selected_id=None, next_id="job_0002")
check("a queued job still wins over a stopped one",
      jid is None and label == "Start queue", f"got {jid!r} {label!r}")

jid, label, on = scenario(DONE, selected_id=None, next_id=None)
check("a done job does not make Start light up", not on,
      "re-rendering hours of finished tiles is not a one-click action")

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
