"""
Phase 7: installability, the file browser, creating jobs from the UI, and the
preview size guard.

    python tests\test_phase7.py
"""

import io
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

TMP = tempfile.mkdtemp(prefix="phase7_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.PORT = 8796
config.PUBLIC_HOSTNAME = ""
config.ensure_dirs()

import importlib                      # noqa: E402
import browse                         # noqa: E402
import queue_manager as qm            # noqa: E402
import server as server_mod           # noqa: E402
importlib.reload(server_mod)

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def get(path):
    try:
        r = urllib.request.urlopen(
            f"http://127.0.0.1:{config.PORT}{path}", timeout=10)
        return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""


def post(path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{config.PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return 200, json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


srv = ThreadingHTTPServer(("127.0.0.1", config.PORT), server_mod.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.4)

print("\n=== 1. installability: the service worker must control the root ===")
code, body, ctype = get("/sw.js")
check("service worker served from the ROOT", code == 200, f"HTTP {code}")
check("served as javascript", "javascript" in ctype, ctype)
code, body, _ = get("/")
html = body.decode("utf-8", "replace")
check("page registers /sw.js, not /static/sw.js",
      'register("/sw.js"' in html and 'register("/static/sw.js"' not in html,
      "a worker under /static/ cannot control start_url, so Chrome "
      "never offers Install app")
man = json.loads(get("/static/manifest.webmanifest")[1])
check("manifest has an id", man.get("id") == "/", str(man.get("id")))
check("scope covers the app", man.get("scope") == "/")
check("manifest link uses credentials",
      'rel="manifest"' in html and 'crossorigin="use-credentials"' in html,
      "without it the manifest is fetched with credentials omitted, so behind "
      "Cloudflare Access the browser gets the login page instead of the JSON "
      "and the app is not installable")

print("\n=== 2. file browser ===")
scenes = os.path.join(TMP, "scenes", "alps")
os.makedirs(scenes, exist_ok=True)
blend = os.path.join(scenes, "valley.blend")
open(blend, "wb").write(b"x" * 2048)
open(os.path.join(scenes, "notes.txt"), "wb").write(b"ignore me")

code, body, _ = get("/api/browse")
root = json.loads(body)
check("root listing offers drives", code == 200 and
      any(e["path"].endswith(":\\") for e in root["entries"]))

code, body, _ = get("/api/browse?path=" + urllib.parse.quote(scenes))
d = json.loads(body)
names = [e["name"] for e in d["entries"]]
check("lists the .blend", "valley.blend" in names, str(names))
check("hides non-blend files", "notes.txt" not in names, str(names))
check("gives a real absolute path",
      any(e.get("path") == blend for e in d["entries"]))
check("reports size for pickers", any(e.get("mb") is not None
                                      for e in d["entries"] if not e["dir"]))
check("offers a way back up", d.get("parent") == os.path.dirname(scenes))

code, body, _ = get("/api/browse?path=" + urllib.parse.quote(
    os.path.join(TMP, "does-not-exist")))
check("missing folder returns an error, not a crash",
      "error" in json.loads(body), body[:80])

print("\n=== 3. creating a job from the UI ===")
code, d = post("/api/new", {"blend": blend, "kind": "tiled",
                            "long_edge": 4000, "tile_long": 512,
                            "format": "JPEG", "render_name": "uitest"})
check("tiled job created", code == 200 and d.get("job"), str(d)[:120])
job = qm.load_job(d["job"]["id"]) if d.get("job") else {}
check("settings applied", job.get("config", {}).get("LONG_EDGE") == 4000,
      str(job.get("config")))
check("pinned to the tiled script", "render_tiles" in job.get("script", ""))

code, d = post("/api/new", {"blend": blend, "kind": "animation",
                            "frame_start": 5, "frame_end": 20})
check("animation job created", code == 200 and
      qm.load_job(d["job"]["id"])["config"]["FRAME_START"] == 5)
check("classic jobs pin the classic script",
      "render_classic" in qm.load_job(d["job"]["id"])["script"])

code, d = post("/api/new", {"blend": blend, "kind": "still", "frame": 7})
check("still job created",
      code == 200 and qm.load_job(d["job"]["id"])["config"]["FRAME"] == 7)

code, d = post("/api/new", {"blend": os.path.join(TMP, "nope.blend"),
                            "kind": "tiled"})
check("missing file refused", code == 400 and "not found" in d.get("error", ""),
      str(d))
code, d = post("/api/new", {"blend": os.path.join(scenes, "notes.txt"),
                            "kind": "tiled"})
check("non-blend refused", code == 400, str(d))

print("\n=== 3b. adding renders is local-only ===")


def remote(method, path, payload=None):
    """Same request, but arriving with the tunnel's hostname."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{config.PORT}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method)
    req.add_header("Host", "render.example.com")
    try:
        return 200, json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


real_public = config.PUBLIC_HOSTNAME
config.PUBLIC_HOSTNAME = ""          # keep the Access guard out of this test

code, d = remote("GET", "/api/browse")
check("remote CANNOT browse the filesystem", code == 403, f"HTTP {code} {d}")
code, d = remote("POST", "/api/new", {"blend": blend, "kind": "tiled"})
check("remote CANNOT create a render", code == 403, f"HTTP {code} {d}")
check("and says why", "render PC" in (d.get("error") or ""), str(d.get("error")))

code, d = remote("GET", "/api/status")
check("remote CAN still watch", code == 200 and d.get("local") is False)
code, d = remote("POST", "/api/start", {})
check("remote CAN still steer (start/stop/resume)", code == 200,
      f"HTTP {code} — watching and steering is the whole point of remote")
config.PUBLIC_HOSTNAME = real_public

html = get("/")[1].decode("utf-8", "replace")
check("UI hides the New render button remotely", 'id="newwrap" hidden' in html)

print("\n=== 4. warns when a job would resume into an existing folder ===")
out = os.path.join(scenes, "tiles", "uitest")
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "tile_r0000_c0000.jpg"), "wb").write(b"x")
code, d = post("/api/new", {"blend": blend, "kind": "tiled",
                            "render_name": "uitest"})
check("job still created", code == 200 and d.get("job"))
check("but warns about the shared output folder",
      "resume into that folder" in (d.get("warning") or ""), str(d.get("warning")))

print("\n=== 5. preview size guard ===")
from PIL import Image                      # noqa: E402

small = os.path.join(TMP, "small.png")
Image.new("RGB", (800, 600), (40, 80, 120)).save(small)
data, skip = server_mod.thumbnail(small, os.path.getmtime(small))
check("normal image previews", data is not None and skip is None,
      f"{len(data) if data else 0} bytes")
with Image.open(io.BytesIO(data)) as im:
    check("thumbnail is downscaled", max(im.size) <= config.PREVIEW_MAX_PX,
          f"{im.size}")

# Pretend the image is enormous rather than actually making one.
real_mp = config.PREVIEW_MAX_MP
config.PREVIEW_MAX_MP = 0.1
data, skip = server_mod.thumbnail(small, os.path.getmtime(small) + 1)
check("oversized image is skipped, not decoded", data is None and skip,
      str(skip))
check("skip reason explains itself", "preview skipped" in (skip or ""), str(skip))
config.PREVIEW_MAX_MP = real_mp

real_mb = config.PREVIEW_MAX_MB
config.PREVIEW_MAX_MB = 0.0001
data, skip = server_mod.thumbnail(small, os.path.getmtime(small) + 2)
check("oversized FILE is skipped before Pillow even opens it",
      data is None and "MB file" in (skip or ""), str(skip))
config.PREVIEW_MAX_MB = real_mb

check("UI has a preview toggle", 'id="previewon"' in html)
check("UI handles the 415 skip", "415" in html)

srv.shutdown()
print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
