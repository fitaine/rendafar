"""
Sample count and Blender's ETA in the header, in place of the frame count.

For a still, counting frames says nothing: the header read "0/1 frames (0.0%)"
for an hour and then jumped to done. Samples describe the same work in terms
that move, so they replace it.

The rule that had to change first: `live` used to be discarded after 45s of
silence, on the assumption that progress arrives continuously. It does not.
Cycles reports in sample BATCHES — measured, a 27s render reported every 13s
and a 107s render every 27s, the gap growing with the render — so on a 90
minute still there is one reading every 20 to 40 minutes. At 45s the sample
count would have shown for under a minute and then vanished for half an hour.

Between readings, Blender's own "Remaining" is counted down by however long
ago it was said, and anything older than two minutes carries its age, so a
figure that has not moved is never passed off as live.

    python tests\test_live_samples.py
"""

import os
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

import runner                                   # noqa: E402
from desktop import theme                       # noqa: E402
theme.apply(app)
from desktop.main_window import MainWindow      # noqa: E402

MainWindow._settings = lambda self: QtCore.QSettings(
    os.path.join(tempfile.mkdtemp(prefix="live_"), "s.ini"),
    QtCore.QSettings.IniFormat)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


win = MainWindow()
win.poller.stop()

STILL = {"done": 0, "total": 1, "unit": "frames", "percent": 0.0}
TILED = {"done": 412, "total": 900, "unit": "tiles", "percent": 45.8,
         "eta_s": 46721, "eta_at": None, "avg_tile_s": 92}
ANIM = {"done": 114, "total": 300, "unit": "frames", "percent": 38.0,
        "eta_s": 42000, "eta_at": None, "avg_tile_s": 228}


def header(live, prog, mode="still"):
    win.on_status({"busy": True, "queue": [], "job_id": "j", "queue_eta": None,
                   "job": {"id": "j", "name": "n", "type": "classic",
                           "state": "running", "blend": "x.blend",
                           "config": {"MODE": mode}},
                   "live": live, "progress": prog, "session_s": 5400})
    return win.meta_label.text()


print("\n=== 1. the frame count is gone for a still ===")
for label, live in (("with samples", {"sample": 1025, "samples_total": 2048,
                                      "percent": 50.0, "remaining_s": 754,
                                      "age_s": 3}),
                    ("without samples", None)):
    txt = header(live, STILL)
    check(f"no '0/1 frames' {label}", "0/1 frames" not in txt, txt)

print("\n=== 2. what replaces it ===")
txt = header({"sample": 1025, "samples_total": 2048, "percent": 50.0,
              "remaining_s": 754, "age_s": 3}, STILL)
check("the sample count is shown", "sample 1 025/2 048" in txt, txt)
check("...with a percentage that actually moves", "(50.0%)" in txt, txt)
check("...and Blender's ETA", "ETA 12m 34s" in txt, txt)

print("\n=== 3. a reading is never passed off as fresher than it is ===")
fresh = header({"sample": 1025, "samples_total": 2048, "percent": 50.0,
                "remaining_s": 754, "age_s": 3}, STILL)
check("a fresh reading carries no age", "as of" not in fresh, fresh)
old = header({"sample": 1025, "samples_total": 2048, "percent": 50.0,
              "remaining_s": 1080, "age_s": 1320}, STILL)
check("one 22 minutes old says so", "as of 22m 00s ago" in old, old)

print("\n=== 4. nothing else changed ===")
t = header(None, TILED)
check("a tiled render still counts tiles", "412/900 tiles (45.8%)" in t, t)
check("...and keeps its own ETA and per-tile time",
      "ETA 12h 58m" in t and "1m 32s/tile" in t, t)
a = header(None, ANIM, mode="animation")
check("an animation still counts frames", "114/300 frames (38.0%)" in a, a)

print("\n=== 5. the staleness window ===")
check("live readings survive far longer than 45s",
      runner.LIVE_STALE_S >= 1800,
      f"Cycles reports every 20-40 minutes on a long still; at the old 45s the "
      f"count would vanish between readings.  got {runner.LIVE_STALE_S}")

print("\n=== 6. Blender's clock strings ===")
for text, want in (("00:12.34", 12.34), ("01:17.07", 77.07),
                   ("1:02:03", 3723.0), ("", None), (None, None),
                   ("nonsense", None)):
    got = runner._clock_to_s(text)
    ok = (abs(got - want) < 0.01) if (got is not None and want is not None) \
        else got == want
    check(f"{text!r} -> {want}", ok, f"got {got}")

print("\n=== 7. the countdown between readings ===")
r = runner.Runner()
r._proc = type("P", (), {"pid": 1, "poll": lambda self: None})()
r._job_id = "j"
r._started = 0
import time as _t
r._live = {"at": _t.time() - 300, "sample": 1025, "samples_total": 2048,
           "remaining": "00:10:00", "percent": 50.0}
live = r.status()["live"]
check("the reading is kept, not dropped", live is not None)
check("...its age is reported", 295 <= live["age_s"] <= 305, str(live.get("age_s")))
check("...and the ETA is counted down by that age",
      295 <= (600 - live["remaining_s"]) <= 305,
      f"600s said 5 minutes ago should read about 300s.  got {live['remaining_s']}")

# Blender said "one minute left" ten minutes ago — it underestimated, which
# happens. Still inside the staleness window, so the reading is kept.
r._live = {"at": _t.time() - 600, "sample": 1, "samples_total": 2048,
           "remaining": "00:01:00", "percent": 0.0}
check("an ETA that has run out shows zero, never negative",
      r.status()["live"]["remaining_s"] == 0,
      str(r.status()["live"]["remaining_s"]))

r._live = {"at": _t.time() - (runner.LIVE_STALE_S + 60), "sample": 1,
           "samples_total": 2048, "remaining": "00:01:00", "percent": 0.0}
check("but a reading past the staleness window is dropped entirely",
      r.status()["live"] is None,
      "an hour with no word from Blender is not a live reading any more")

win.close()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
