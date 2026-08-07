"""
Duplicating a job, and starting the queue in one click.

Two asks from the same session:

  * queue one scene several times with a different camera each, without
    picking the .blend again and waiting on another probe of a multi-gigabyte
    file
  * press Start and have it go, instead of selecting a row first

    python tests\test_duplicate.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="dup_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()

import paths                        # noqa: E402
import queue_manager as qm          # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


BLEND = os.path.join(TMP, "Bure.blend")
open(BLEND, "wb").write(b"x")


def fresh():
    for f in os.listdir(config.QUEUE_DIR):
        os.remove(os.path.join(config.QUEUE_DIR, f))
    qm.save_state({"order": [], "running": None, "running_pid": None})


print("\n=== 1. the copy is the same render, one setting away ===")
fresh()
src = qm.create_job(BLEND, name="Bure", job_type="tiled",
                    cfg={"LONG_EDGE": 120000, "TILE_LONG": 4096,
                         "FILE_FORMAT": "WEBP"})
qm.set_state(src["id"], qm.STOPPED)
src = qm.load_job(src["id"])
copy = qm.duplicate(src["id"])

check("it is a new job, not the same one", copy["id"] != src["id"])
check("same .blend, not a copy of the file", copy["blend"] == src["blend"])
check("same settings carried over",
      copy["config"]["LONG_EDGE"] == 120000
      and copy["config"]["TILE_LONG"] == 4096)
check("keeps the ORIGINAL's pinned script",
      copy["script"] == src["script"],
      "a duplicate is the same render with one setting changed, so it must "
      "run the same bytes — silently upgrading it to today's snapshot is the "
      "one thing the pinning exists to prevent")

print("\n=== 2. it starts clean, and waiting ===")
check("the copy is queued, so the queue will reach it",
      copy["state"] == qm.QUEUED,
      f"got {copy['state']!r} — the original was 'stopped'")
check("no history is inherited", copy["history"] == [],
      "'stopped at 691/900' describes the original's work, not the copy's")
check("it is named as a copy", copy["name"] == "Bure (2)", copy["name"])
check("a second copy counts on", qm.duplicate(src["id"])["name"] == "Bure (3)")

print("\n=== 3. it lands next to what it came from ===")
fresh()
a = qm.create_job(BLEND, name="A")
b = qm.create_job(BLEND, name="B")
c = qm.create_job(BLEND, name="C")
copy = qm.duplicate(a["id"])
check("the copy sits directly below the original, not at the end",
      qm.load_state()["order"] == [a["id"], copy["id"], b["id"], c["id"]],
      f"copies of one scene belong together.  {qm.load_state()['order']}")

print("\n=== 4. the copy does not eat the original's tiles ===")
# Two tiled jobs on one .blend with the same RENDER_NAME share a folder: the
# copy finds every tile already there, skips them all, and "finishes" in
# seconds having rendered nothing.
fresh()
src = qm.create_job(BLEND, name="Bure", job_type="tiled",
                    cfg={"LONG_EDGE": 120000, "TILE_LONG": 4096})
copy = qm.duplicate(src["id"])
check("the copy gets its own RENDER_NAME",
      copy["config"].get("RENDER_NAME") == "Bure (2)",
      f"got {copy['config'].get('RENDER_NAME')!r}")
check("...so it writes somewhere else entirely",
      paths.job_output_dir(copy) != paths.job_output_dir(src),
      f"{paths.job_output_dir(copy)}")
check("...and a third copy still does not collide",
      len({paths.job_output_dir(j)
           for j in (src, copy, qm.duplicate(src["id"]))}) == 3)

print("\n=== 5. classic copies cannot be separated automatically ===")
# A classic render takes its output path from the .blend, so there is no
# RENDER_NAME to vary — the copy genuinely does share the folder until the
# user changes it, and that has to be said rather than discovered.
fresh()
src = qm.create_job(BLEND, name="Anim", job_type="classic",
                    cfg={"MODE": "animation"})
copy = qm.duplicate(src["id"])
check("a classic copy carries the same output path",
      paths.job_output_dir(copy) == paths.job_output_dir(src),
      "the server returns a warning for exactly this case")
# render_classic imports bpy at module level, so it can only be read as text
# out here — importing it would need Blender.
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
classic_src = open(os.path.join(APP, "render_classic.py"), encoding="utf-8").read()
overridable = classic_src[classic_src.index("_JOB_OVERRIDABLE"):][:260]
check("...and CAMERA is overridable for classic, which is what makes "
      "camera-per-copy work at all",
      '"CAMERA"' in overridable, overridable.split("}")[0])

tiled_src = open(os.path.join(APP, "frozen",
                              "render_tiles_unlimited_2026-07-26.py"),
                 encoding="utf-8").read()
tiled_over = tiled_src[tiled_src.index("_JOB_OVERRIDABLE"):][:320]
check("but the TILED script has no CAMERA override",
      '"CAMERA"' not in tiled_over,
      "it always uses the scene camera, so duplicating a tiled job gives a "
      "second render of the SAME camera — flagged rather than pretended")

print("\n=== 6. guards ===")
try:
    qm.duplicate("job_9999")
    check("duplicating a job that does not exist raises", False)
except KeyError:
    check("duplicating a job that does not exist raises", True)

fresh()
run = qm.create_job(BLEND, name="Running")
qm.set_state(run["id"], qm.RUNNING)
copy = qm.duplicate(run["id"])
check("a running job can be duplicated (it only writes a new job file)",
      copy["state"] == qm.QUEUED,
      "queueing up another camera mid-render is the main use")
check("...and the original keeps rendering",
      qm.load_job(run["id"])["state"] == qm.RUNNING)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
