"""
A removed job must not leave its manifest behind, and ids must never repeat.

Found live, and it corrupted three things at once from a single cause. A
manifest is named after its job id. remove() deleted job_0006.json but left
job_0006_manifest.json, and _next_id() counted only job_NNNN.json files — so
removing the newest job lowered the maximum and the next job took its number
back, inheriting a dead render's manifest.

What that looked like, all reported by the user:

  * a still 11 minutes into rendering showed "0 of 1 frames done" and an ETA
    of 984s — carried from a deleted Ferme-des-Crozets camera
  * the preview showed a finished Crozets frame, because the render script
    carries `last_file` forward from the previous manifest
  * render history recorded 21.2 and 16.4 minutes against a scene that had
    never finished a render, because the stale frame_times were read as its own

The lesson worth keeping: an id that identifies a FILE cannot be recycled.

    python tests\test_manifest_reuse.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="reuse_")
config.QUEUE_DIR = TMP
config.LOGS_DIR = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(TMP, "state.json")
config.ensure_dirs()

import paths                        # noqa: E402
import queue_manager as qm          # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


BLEND = os.path.join(TMP, "x.blend")
open(BLEND, "wb").write(b"x")


def manifest_for(job_id, seconds=1234.5, last_file="D:\\old\\Crozets_0001.png"):
    """Pretend this job rendered a frame."""
    p = paths.classic_manifest_path({"id": job_id})
    json.dump({"kind": "frames", "unit": "frames", "total": 1,
               "frames_done": 1, "frame_times": {"1": seconds},
               "grid": "1", "frames": [1, 1], "last_file": last_file,
               "output_dir": "D:\\old"}, open(p, "w", encoding="utf-8"))
    return p


print("\n=== 1. removing a job removes its manifest ===")
a = qm.create_job(BLEND, name="first", job_type="classic", cfg={"MODE": "still"})
mp = manifest_for(a["id"])
check("the manifest is there while the job is", os.path.isfile(mp))
qm.remove(a["id"])
check("removing the job removes it too", not os.path.isfile(mp),
      "left behind, it is inherited by whatever job takes that id next")

print("\n=== 2. ids are never handed out twice ===")
seen = []
for _ in range(4):
    j = qm.create_job(BLEND, name="n", job_type="classic", cfg={"MODE": "still"})
    seen.append(j["id"])
    qm.remove(j["id"])                     # remove each one straight away
check("four jobs created and removed get four different ids",
      len(set(seen)) == 4,
      f"counting only the job files, removing the newest lowers the maximum "
      f"and the next job takes its number back.  got {seen}")

print("\n=== 3. the exact live failure, reproduced ===")
first = qm.create_job(BLEND, name="Crozets camera", job_type="classic",
                      cfg={"MODE": "still"})
manifest_for(first["id"], seconds=984.16,
             last_file="D:\\Crozets\\Camera.006_main_0001.png")
qm.remove(first["id"])

second = qm.create_job(BLEND, name="a different scene", job_type="classic",
                       cfg={"MODE": "still"})
check("the new job does not take the removed job's id",
      second["id"] != first["id"], f"{first['id']} then {second['id']}")

prog = paths.progress(second)
check("...so it starts with no progress of its own",
      not prog or not (prog.get("done") or prog.get("avg_tile_s")),
      f"a fresh render reported 0/1 frames done AND an ETA at the same time, "
      f"which cannot both be true.  got {prog}")

import history                      # noqa: E402
check("...and teaches history nothing it did not earn",
      history.estimate(second) is None,
      "21.2 and 16.4 minutes were recorded against a scene that had never "
      "finished a render")

print("\n=== 4. an orphan manifest cannot be picked up even so ===")
# Belt and braces: if a manifest survives some other way — a crash between the
# two unlinks, a file restored from a backup — the id is still not reused.
stray = paths.classic_manifest_path({"id": "job_0099"})
json.dump({"kind": "frames", "total": 1, "frames_done": 1,
           "frame_times": {"1": 999.0}}, open(stray, "w", encoding="utf-8"))
nxt = qm.create_job(BLEND, name="after stray", job_type="classic",
                    cfg={"MODE": "still"})
check("a job created after a stray manifest skips past its number",
      nxt["id"] > "job_0099", f"got {nxt['id']}")
check("...so it has no progress either",
      not (paths.progress(nxt) or {}).get("done"))

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
