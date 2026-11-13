"""
A classic job whose output Blender cannot save to is refused before it renders.

job_0045 rendered for 10 minutes and then could not save: its Output was
"Chamechaude.tif_Camera.002_the_print_one_", with no folder, so Blender
resolved it against the folder it was started from — C:\\ — and Windows
refused the write. The log printed "output : C:\\" at the start; nothing
acted on it.

The check sits at three points: adding a job, editing its Output (or Scene),
and starting it, which covers jobs queued before the check existed. At start a
bad job is marked failed and the queue moves on to the next.

No Blender needed — the .blend probe is replaced with a stub.

    python tests\\test_output_check.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="outcheck_")
config.QUEUE_DIR = TMP
config.LOGS_DIR = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(TMP, "state.json")
config.AUTO_START_NEXT = False
config.ensure_dirs()

import blend_info                   # noqa: E402
import paths                        # noqa: E402
import queue_manager as qm          # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


BLEND = os.path.join(TMP, "scene.blend")
open(BLEND, "wb").write(b"x")

# What the probe reports for the .blend's own output path. bpy.path.abspath
# only expands "//", so a bare name comes back exactly as it was typed.
BLEND_PATH = {"value": r"C:\renders\ok_"}


def fake_read(path, refresh=False):
    s = {"scene": "Scene", "engine": "CYCLES", "device": "GPU",
         "filepath": BLEND_PATH["value"], "cameras": ["Cam"], "camera": "Cam"}
    return dict(s, active_scene="Scene", scenes=[s])


blend_info.read = fake_read

print("\n=== 1. which paths are refused ===")
P = paths.output_problem
check("the path that lost the render is refused",
      P("Chamechaude.tif_Camera.002_the_print_one_") is not None)
check("...and the reason names the path",
      "Chamechaude.tif_Camera.002_the_print_one_" in (P("Chamechaude.tif_Camera.002_the_print_one_") or ""))
check("Blender's own default /tmp\\ is refused — it has no drive either",
      P("/tmp\\") is not None)
check("an empty path is refused", P("") is not None)
check("a relative folder is refused", P(r"renders\shot_") is not None)
check("a drive-relative path (C:renders) is refused", P("C:renders\\") is not None)
check("a full path is accepted", P(r"C:\Users\T\Pictures\renders\shot_") is None)
check("...with forward slashes too", P("D:/renders/shot_") is None)
check("a network path is accepted", P(r"\\nas\renders\shot_") is None)
check("// (beside the .blend) is accepted", P("//renders/shot_") is None)

print("\n=== 2. job Output first, else the .blend's ===")
O = blend_info.output_problem
BLEND_PATH["value"] = "Chamechaude.tif"
check("no Output: the .blend's bare name is refused", O(BLEND, {}) is not None)
check("...and the reason says it is the .blend's",
      ".blend" in (O(BLEND, {}) or ""))
check("a full job Output overrides the .blend's bad one",
      O(BLEND, {"OUTPUT_DIR": r"C:\renders\x_"}) is None)
BLEND_PATH["value"] = r"C:\renders\ok_"
check("a bad job Output is refused even when the .blend's is fine",
      O(BLEND, {"OUTPUT_DIR": "x_"}) is not None)
check("an unreadable .blend is not blocked on",
      O(BLEND, {}, info={"error": "Blender took too long"}) is None)

print("\n=== 3. adding a job ===")
import server                       # noqa: E402


class H:
    _json = staticmethod(lambda d, code=200: d)
    _make_output_unique = staticmethod(server.Handler._make_output_unique)
    EDITABLE = server.Handler.EDITABLE


def add(body):
    try:
        return server.Handler._new_job(H(), body), None
    except ValueError as e:
        return None, str(e)


before = len(qm.all_jobs())
_, err = add({"blend": BLEND, "kind": "still", "camera": "Cam",
              "output_dir": "Chamechaude.tif"})
check("a still with a bare-name Output is refused", err and "no folder" in err, err)
check("...and no job is left behind", len(qm.all_jobs()) == before)

BLEND_PATH["value"] = "Chamechaude.tif"
_, err = add({"blend": BLEND, "kind": "animation"})
check("an animation on a .blend with a bare-name path is refused", bool(err), err)
BLEND_PATH["value"] = r"C:\renders\ok_"

d, err = add({"blend": BLEND, "kind": "still", "camera": "Cam",
              "output_dir": r"C:\renders\shot_"})
check("a still with a full Output is added", d and not err, err)

d, err = add({"blend": BLEND, "kind": "tiled"})
check("tiled jobs are not affected", d and not err, err)

print("\n=== 4. editing a job ===")
good = qm.create_job(BLEND, job_type="classic",
                     cfg={"MODE": "still", "OUTPUT_DIR": r"C:\renders\a_"})


def edit(job_id, cfg):
    try:
        return server.Handler._edit_job(H(), {"job_id": job_id, "config": cfg}), None
    except ValueError as e:
        return None, str(e)


_, err = edit(good["id"], {"_OUTPUT": "renders"})
check("typing a folder with no drive into Output is refused", bool(err), err)
check("...and the job keeps its old Output",
      qm.load_job(good["id"])["config"]["OUTPUT_DIR"] == r"C:\renders\a_")

BLEND_PATH["value"] = "Chamechaude.tif"
_, err = edit(good["id"], {"OUTPUT_DIR": ""})
check("clearing Output back to a .blend with a bare-name path is refused",
      bool(err), err)
BLEND_PATH["value"] = r"C:\renders\ok_"

_, err = edit(good["id"], {"_OUTPUT": r"D:\elsewhere"})
check("a full folder is accepted", not err, err)

print("\n=== 5. starting a job already in the queue ===")
import controller as ctl            # noqa: E402

for j in qm.all_jobs():                  # only the two below should be queued
    qm.set_state(j["id"], qm.DONE)
bad1 = qm.create_job(BLEND, job_type="classic",
                     cfg={"MODE": "still", "OUTPUT_DIR": "Chamechaude.tif_Cam_"})
bad2 = qm.create_job(BLEND, job_type="classic",
                     cfg={"MODE": "still", "OUTPUT_DIR": "other_"})
told = []
c = ctl.Controller(notify=lambda ev, job, note=None: told.append((ev, job["id"])))
started = c.start_next()
check("nothing starts when every queued job has a bad path", started is None)
for b in (bad1, bad2):
    j = qm.load_job(b["id"])
    check(f"{b['id']} is marked failed", j["state"] == qm.FAILED, j["state"])
    check("...with the reason in its history",
          "no folder" in (j["history"][-1].get("note") or ""),
          j["history"][-1])
check("the queue moved past the first to reach the second",
      [e for e in told] == [(qm.FAILED, bad1["id"]), (qm.FAILED, bad2["id"])], told)
check("Blender was never launched", not c.runner.is_busy())

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
