"""
The web server.

Standard library only — no FastAPI, no uvicorn, no pip install.  This process
has to start unattended at boot after a power cut, and every dependency is one
more thing that can fail there.  For one user on a private network,
ThreadingHTTPServer is enough.

    python server.py

Binds to localhost only.  Remote access arrives through Cloudflare Tunnel:
`cloudflared` runs on this machine and connects over 127.0.0.1, and Cloudflare
Access authenticates before any request is forwarded down the tunnel.  The app
itself has no login, so it must never listen on a network interface.
"""

import io
import json
import mimetypes
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config
import gpu
import notify
import paths
import queue_manager as qm
from controller import Controller

PORT       = config.PORT
STATIC_DIR = os.path.join(config.APP_DIR, "static")
TILE_RE    = re.compile(r"tile_r(\d+)_c(\d+)\.")

controller = Controller(notify=notify.job_event)

_thumb_cache = {"key": None, "bytes": None}
_thumb_lock  = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────────────────

def tile_bitmap(job):
    """
    One character per cell: '1' rendered, '0' not. Row-major.

    Classic jobs record the bitmap directly. Tiled jobs are derived from
    manifest.json's tile_times keys rather than by listing the folder — no
    directory scan of 900 files on every poll, and it is exactly what the
    render script considers done.
    """
    m = paths.read_manifest(job)
    if not m:
        return None
    cols, rows = m.get("cols") or 0, m.get("rows") or 0
    if not (cols and rows):
        return None
    if m.get("grid"):
        return {"cols": cols, "rows": rows, "grid": m["grid"],
                "unit": m.get("unit", "frames")}
    grid = bytearray(b"0" * (cols * rows))
    for fname in (m.get("tile_times") or {}):
        hit = TILE_RE.search(fname)
        if not hit:
            continue
        r, c = int(hit.group(1)), int(hit.group(2))
        if 0 <= r < rows and 0 <= c < cols:
            grid[r * cols + c] = ord("1")
    return {"cols": cols, "rows": rows, "grid": grid.decode(), "unit": "tiles"}


def thumbnail(path, mtime, max_px=1000):
    """Downscaled JPEG of a render tile, cached on (path, mtime)."""
    key = (path, mtime)
    with _thumb_lock:
        if _thumb_cache["key"] == key:
            return _thumb_cache["bytes"]
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=86)
            data = buf.getvalue()
    except Exception:
        return None
    with _thumb_lock:
        _thumb_cache["key"], _thumb_cache["bytes"] = key, data
    return data


def build_status():
    st  = controller.status()
    job = st.get("job")
    prog = st.get("progress") or {}
    st["gpu"] = gpu.read(rendering=st["busy"],
                         avg_tile_s=prog.get("avg_tile_s"))

    if job:
        st["output_dir"] = paths.job_output_dir(job)
        st["tiles"]      = tile_bitmap(job)
        lt = paths.latest_tile(job)
        if lt:
            st["last_tile"] = {"name": os.path.basename(lt[0]),
                               "age_s": round(time.time() - lt[1]),
                               "mtime": lt[1]}
    # Queue rows carry their own output path — two jobs on one .blend with the
    # same RENDER_NAME share tiles, and that must be visible, not surprising.
    for j in st.get("queue", []):
        try:
            j["output_dir"] = paths.job_output_dir(j)
        except Exception:
            j["output_dir"] = None
    st["log"] = controller.runner.log_tail(400)
    return st


# ── HTTP ─────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                       # the console belongs to render output

    def _access_ok(self):
        """
        Fail closed if the Cloudflare tunnel is live without Access in front.

        Requests that came down the tunnel carry the public hostname in Host.
        With Access configured, Cloudflare attaches Cf-Access-Jwt-Assertion to
        every authenticated request; without Access, the header is absent — so
        its absence on a tunnel request means the front door is open and we
        refuse to serve. Local requests (Host = 127.0.0.1) are untouched.
        """
        pub = getattr(config, "PUBLIC_HOSTNAME", "")
        if not pub:
            return True
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host != pub.lower():
            return True
        return bool(self.headers.get("Cf-Access-Jwt-Assertion"))

    # -- responses --

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", cache=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, default=str), "application/json")

    # -- routing --

    def do_GET(self):
        if not self._access_ok():
            return self._send(403, "Cloudflare Access required")
        u    = urlparse(self.path)
        path = u.path
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/api/status":
                return self._json(build_status())
            if path == "/api/log":
                n = int(parse_qs(u.query).get("n", ["400"])[0])
                return self._json({"lines": controller.runner.log_tail(n)})
            if path == "/api/tile":
                return self._tile()
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            return self._send(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        if not self._access_ok():
            return self._send(403, "Cloudflare Access required")
        path = urlparse(self.path).path
        try:
            n    = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}

            if path == "/api/stop":
                return self._json({"stopped": controller.stop()})
            if path == "/api/start":
                job = (controller.start(body["job_id"]) if body.get("job_id")
                       else controller.start_next())
                return self._json({"started": job["id"] if job else None})
            if path == "/api/resume":
                return self._json({"job": qm.resume(body["job_id"])})
            if path == "/api/move":
                return self._json({"order": qm.move(body["job_id"],
                                                    int(body["offset"]))})
            if path == "/api/remove":
                qm.remove(body["job_id"])
                return self._json({"removed": body["job_id"]})
            return self._send(404, "not found")
        except (KeyError, ValueError, RuntimeError, FileNotFoundError) as e:
            return self._json({"error": str(e)}, 400)
        except BrokenPipeError:
            pass
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # -- handlers --

    def _tile(self):
        st  = controller.status()
        job = st.get("job")
        if not job:
            for j in qm.all_jobs():
                if j["state"] in (qm.STOPPED, qm.INTERRUPTED, qm.FAILED, qm.DONE):
                    job = j
                    break
        if not job:
            return self._send(404, "no tile yet")
        lt = paths.latest_tile(job)
        if not lt:
            return self._send(404, "no tile yet")
        data = thumbnail(lt[0], lt[1])
        if not data:
            return self._send(404, "tile unreadable")
        return self._send(200, data, "image/jpeg")

    def _static(self, rel):
        rel  = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            return self._send(404, "not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            return self._send(200, f.read(), ctype)


def main():
    config.ensure_dirs()

    # Boot recovery: any job left 'running' with no live Blender behind it is
    # marked interrupted, re-queued at the front, and started again — this is
    # the whole point of the app surviving a power cut.
    found, started = controller.recover_and_resume()
    if found:
        print(f"  recovered {len(found)} interrupted job(s): {', '.join(found)}")
        if started:
            print(f"  auto-resumed: {started['id']} — {started['name']}")
    print(f"  telegram        :  {'on' if notify.enabled() else 'off (no secrets_local.py)'}")

    # Localhost only. Remote access arrives through cloudflared, which runs on
    # this machine and connects over 127.0.0.1 — the app is never on the LAN.
    srv = ThreadingHTTPServer((config.HOST, PORT), Handler)
    print(f"\n  Rendafar  →  http://127.0.0.1:{PORT}")
    print(f"  remote          →  via Cloudflare Tunnel (see PLAN.md)")
    print(f"  machine         :  {socket.gethostname()}")
    print(f"  queue           :  {config.QUEUE_DIR}")
    print("  Ctrl+C to stop the server (renders keep running)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  server stopped — any running render continues")


if __name__ == "__main__":
    main()
