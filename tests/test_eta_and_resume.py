"""
Three things asked for after using the app on a real render:

  1. the Telegram "started" message should say which frames a resumed
     animation is actually picking up, not just repeat the configured range
  2. ETA should say what time it will be finished, not only how long is left
  3. (covered in test_dock_layout) the column divider should be one handle

Everything here runs against temporary directories; nothing touches the real
queue or sends anything to Telegram.

    python tests\test_eta_and_resume.py
"""

import datetime
import json
import os
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

import paths
import notify

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


TMP = tempfile.mkdtemp(prefix="eta_resume_")


def make_job(grid, first=1, last=250, total=250, is_movie=False, kind="frames"):
    """A classic job whose manifest says exactly which frames are on disk."""
    d = os.path.join(TMP, f"job_{len(os.listdir(TMP))}")
    os.makedirs(d, exist_ok=True)
    man = {
        "kind": kind, "unit": "frames", "mode": "animation",
        "output_dir": d, "is_movie": is_movie,
        "frames": [first, last], "total": total,
        "frames_done": grid.count("1"), "grid": grid,
        "frame_times": {str(first + i): 12.0
                        for i, c in enumerate(grid) if c == "1"},
        "errors": [],
    }
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f)
    return {"id": "job_0009", "name": "Test anim", "type": "classic",
            "config": {"MODE": "animation", "OUTPUT_DIR": d}, "state": "queued"}


# paths.read_manifest/progress resolve the output dir from the job; point both
# at the manifest we just wrote rather than reimplementing that resolution.
_real_manifest_path = paths.classic_manifest_path
paths.classic_manifest_path = lambda job: os.path.join(
    job["config"]["OUTPUT_DIR"], "manifest.json")

print("\n=== 1. which frames a resumed animation picks up ===")

fresh = make_job("0" * 250)
check("a job with nothing rendered is not called a resume",
      notify._resume_line(fresh) == "",
      f"got {notify._resume_line(fresh)!r}")

part = make_job("1" * 120 + "0" * 130)
line = notify._resume_line(part)
check("a part-done animation reports where it restarts",
      "resuming at frame 121" in line, f"got {line!r}")
check("...and how much is left",
      "130 of 250 frames left" in line, f"got {line!r}")
check("...and does not claim gaps when the range is contiguous",
      "gaps" not in line, f"got {line!r}")

holed = make_job("1" * 50 + "0" + "1" * 69 + "0" * 130)
line = notify._resume_line(holed)
check("a frame that errored mid-run is reported as a gap",
      "gaps earlier" in line,
      f"frame 51 is missing from the middle — 'resuming at 51' alone would "
      f"understate the work.  got {line!r}")
check("...and the count includes the hole",
      "131 of 250 frames left" in line, f"got {line!r}")

print("\n=== 2. frame numbers respect a frame step ===")
# 1,3,5..99 -> 50 frames, step 2. First 10 done means resume at frame 21.
stepped = make_job("1" * 10 + "0" * 40, first=1, last=99, total=50)
todo = notify._frames_todo(stepped)
check("the step is recovered from the endpoints, not assumed to be 1",
      todo[0] == 21 and todo[1] == 23, f"got {todo[:3]}")

print("\n=== 3. the started message says Resumed, not Started ===")
sent = []
notify.send = lambda text: sent.append(text)

notify.job_event("started", fresh)
check("a fresh job still says 'Started'",
      "Started —" in sent[-1] and "Resumed" not in sent[-1], f"got {sent[-1]!r}")

notify.job_event("started", part)
check("a part-done job says 'Resumed'",
      "Resumed —" in sent[-1], f"got {sent[-1]!r}")
check("...and carries the frame detail",
      "resuming at frame 121" in sent[-1], f"got {sent[-1]!r}")
check("...and still names the range, from the manifest",
      "frames 1–250" in sent[-1],
      "FRAME_START/FRAME_END are absent here because the .blend is in "
      f"charge — the range has to come from the manifest.  got {sent[-1]!r}")

print("\n=== 4. tiled renders get a resume line too ===")
# Same idea, different unit — the user's real job is a 30x30 tiled render.
tiled_dir = os.path.join(TMP, "tiled")
os.makedirs(tiled_dir, exist_ok=True)
with open(os.path.join(tiled_dir, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump({"kind": "tiles", "cols": 30, "rows": 30, "tiles_done": 412,
               "tile_times": {str(i): 90.0 for i in range(412)},
               "errors": []}, f)
tiled = {"id": "job_0001", "name": "Gervasutti", "type": "tiled",
         "config": {"OUTPUT_DIR": tiled_dir}, "state": "queued"}
paths.classic_manifest_path = lambda job: os.path.join(
    job["config"]["OUTPUT_DIR"], "manifest.json")
_real_read = paths.read_manifest
paths.read_manifest = lambda job: json.load(
    open(os.path.join(job["config"]["OUTPUT_DIR"], "manifest.json"), encoding="utf-8"))

line = notify._resume_line(tiled)
check("a part-done tiled render reports tiles left",
      "488 of 900 tiles left" in line, f"got {line!r}")

paths.read_manifest = _real_read

print("\n=== 5. ETA as a wall clock, not just a duration ===")
now = datetime.datetime(2026, 7, 28, 21, 0, 0)      # a Tuesday


def clock(**delta):
    at = (now + datetime.timedelta(**delta)).isoformat(timespec="seconds")
    return paths.format_eta_clock(at, now=now)


check("later the same day reads 'today'", clock(hours=2) == "today 23:00",
      f"got {clock(hours=2)!r}")
check("the small hours read 'tomorrow'", clock(hours=8) == "tomorrow 05:00",
      f"got {clock(hours=8)!r}")
check("a couple of days out reads as a weekday",
      clock(days=2) == "Thu 21:00", f"got {clock(days=2)!r}")
check("...which is the point for a 40-80h render",
      clock(hours=63, minutes=20) == "Fri 12:20",
      f"got {clock(hours=63, minutes=20)!r}")
check("past a week the date is spelled out",
      clock(days=9) == "Thu 06 Aug 21:00", f"got {clock(days=9)!r}")
check("a missing eta is empty rather than an error",
      paths.format_eta_clock(None) == "" and paths.format_eta_clock("nonsense") == "")

print("\n=== 6. progress() carries eta_at alongside eta_s ===")
prog = paths.progress(part)
check("a frames job exposes eta_at", prog and prog.get("eta_at"),
      f"got {prog and prog.get('eta_at')!r}")
check("...consistent with eta_s",
      abs((datetime.datetime.fromisoformat(prog["eta_at"])
           - datetime.datetime.now()).total_seconds() - prog["eta_s"]) < 5,
      "the clock time must be now + the remaining duration, or the two "
      "readings on screen would contradict each other")

paths.classic_manifest_path = _real_manifest_path
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
