"""
Editing a queued job: what is allowed, what is refused, and the output-path
round trip.

The round trip is the one that matters. The Output cell shows the folder a
render actually writes to, but a tiled job stores that as OUTPUT_DIR joined
with RENDER_NAME. If the split is wrong, the path silently nests inside itself
and the render writes somewhere nobody looks.

    python tests\test_edit_jobs.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="edit_test_")
config.QUEUE_DIR = os.path.join(TMP, "queue")
config.LOGS_DIR = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.PORT = 8794
config.PUBLIC_HOSTNAME = ""
config.ensure_dirs()

import importlib                      # noqa: E402
import paths                          # noqa: E402
import queue_manager as qm            # noqa: E402
import server as server_mod           # noqa: E402
importlib.reload(server_mod)

sys.path.insert(0, os.path.join(config.APP_DIR, "desktop"))

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def post(path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{config.PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return 200, json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


srv = ThreadingHTTPServer(("127.0.0.1", config.PORT), server_mod.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.4)

blend = os.path.join(TMP, "scene.blend")
open(blend, "wb").write(b"x")

print("\n=== 1. the output round trip (the corrupting one) ===")
# paths.split_output is plain Python, so this needs no Qt at all — which is
# the point of it living there rather than in the table widget.
split = paths.split_output

c = split(r"D:\3D\Ecrins\tiles\Barre des Ecrins", "tiled")
check("tiled output splits into folder + render name",
      c == {"OUTPUT_DIR": r"D:\3D\Ecrins\tiles",
            "RENDER_NAME": "Barre des Ecrins"}, str(c))
c = split(r"D:\3D\Pilat\frames", "classic")
check("classic output is used as-is",
      c == {"OUTPUT_DIR": r"D:\3D\Pilat\frames"}, str(c))
c = split("", "tiled")
check("empty clears both, back to the default",
      c == {"OUTPUT_DIR": "", "RENDER_NAME": ""}, str(c))
c = split("D:\\", "tiled")      # r"D:\" is not a valid raw string
check("a drive root is kept whole rather than split to nothing",
      c.get("OUTPUT_DIR", "").startswith("D:"), str(c))
c = split(r'  "D:\3D\Ecrins\tiles\Ecrins"  ', "tiled")
check("quotes and spaces from a pasted path are stripped",
      c == {"OUTPUT_DIR": r"D:\3D\Ecrins\tiles", "RENDER_NAME": "Ecrins"}, str(c))

print("\n=== 2. the split matches where the render will actually write ===")
job = qm.create_job(blend, name="tiled", cfg={"LONG_EDGE": 4000})
target = os.path.join(TMP, "renders", "Ecrins 120k")
# Exactly what the Output cell sends: one folder, the server splits it.
code, d = post("/api/edit", {"job_id": job["id"], "config": {"_OUTPUT": target}})
check("edit accepted", code == 200 and not d.get("error"), str(d)[:120])
resolved = paths.job_output_dir(qm.load_job(job["id"]))
check("the render will write exactly where the cell said",
      os.path.normpath(resolved) == os.path.normpath(target),
      f"{resolved}\n          wanted {target}")

print("\n=== 3. what may and may not be changed ===")
code, d = post("/api/edit", {"job_id": job["id"], "config": {"FILE_FORMAT": "PNG"}})
check("tiled: format is editable", code == 200, str(d)[:100])
code, d = post("/api/edit", {"job_id": job["id"], "config": {"LONG_EDGE": 9999}})
check("tiled: long edge is refused", code == 400 and "cannot be changed" in d.get("error", ""),
      "the grid is built from it — tiles already on disk would belong to "
      "a different image")

cj = qm.create_job(blend, name="anim", job_type="classic",
                   cfg={"MODE": "animation"})
code, d = post("/api/edit", {"job_id": cj["id"], "config": {"CAMERA": "Camera.004"}})
check("classic: camera is editable", code == 200, str(d)[:100])
check("camera reached the job config",
      qm.load_job(cj["id"])["config"].get("CAMERA") == "Camera.004")
code, d = post("/api/edit", {"job_id": cj["id"], "config": {"FRAME_START": 5,
                                                            "FRAME_END": 40}})
check("classic: frame range is editable", code == 200, str(d)[:100])
for key in ("SAMPLES", "RESOLUTION_X", "FILE_FORMAT"):
    code, d = post("/api/edit", {"job_id": cj["id"], "config": {key: 1}})
    check(f"classic: {key} is refused", code == 400,
          "the .blend is in charge of it")

print("\n=== 4. refused once the job is no longer just queued ===")
qm.set_state(cj["id"], qm.RUNNING)
code, d = post("/api/edit", {"job_id": cj["id"], "config": {"CAMERA": "Camera"}})
check("a running job cannot be edited", code == 400 and "queued" in d.get("error", ""),
      str(d.get("error")))

print("\n=== 5. refused once something has been rendered ===")
qm.set_state(cj["id"], qm.QUEUED)
mpath = paths.classic_manifest_path(qm.load_job(cj["id"]))
os.makedirs(os.path.dirname(mpath), exist_ok=True)
json.dump({"kind": "frames", "total": 100, "frames_done": 12, "cols": 10,
           "rows": 10, "unit": "frames"}, open(mpath, "w"))
code, d = post("/api/edit", {"job_id": cj["id"], "config": {"CAMERA": "Camera"}})
check("a part-rendered job cannot be edited", code == 400, str(d.get("error")))
check("and says why", "already rendered" in (d.get("error") or ""),
      "changing it now would mix two renders in one folder")

srv.shutdown()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
