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

import browse
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

# When the last browser was seen. Any /api/status poll counts as a window
# watching, from this machine or the phone.
_last_seen = time.time()


def idle_watchdog(srv):
    """
    Shut down when nobody is watching and nothing is rendering.

    Launched from the desktop icon the server has no console and no visible
    process, so closing the window would otherwise leave it running for ever.
    This makes the window's X mean what it appears to mean.

    A render in progress always keeps the server alive: it must stay
    supervised, and reachable from the phone. That is the whole point of the
    app, so it outranks tidiness.
    """
    # Check often enough to react promptly, but never busier than every 5s.
    interval = max(1.0, min(5.0, config.IDLE_QUIT_S / 3.0))
    while True:
        time.sleep(interval)
        if config.IDLE_QUIT_S <= 0:
            return
        if controller.runner.is_busy():
            continue
        if time.time() - _last_seen < config.IDLE_QUIT_S:
            continue
        print(f"\n  no window for {config.IDLE_QUIT_S}s and nothing rendering "
              f"— Rendafar is closing.")
        threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()),
                         daemon=True).start()
        return


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


def thumbnail(path, mtime, max_px=None):
    """
    Downscaled JPEG of a render image, cached on (path, mtime).

    Returns (bytes, None) on success, or (None, reason) when the image is too
    big to preview cheaply. Decoding happens on the machine that is rendering,
    so an oversized image is refused rather than served at the cost of the
    render it is meant to be showing.
    """
    max_px = max_px or config.PREVIEW_MAX_PX
    key = (path, mtime, max_px)
    with _thumb_lock:
        if _thumb_cache["key"] == key:
            return _thumb_cache["bytes"], _thumb_cache.get("skip")

    try:
        mb = os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return None, "unreadable"
    if mb > config.PREVIEW_MAX_MB:
        return None, f"{mb:.0f} MB file — preview skipped to spare the render"

    data = skip = None
    try:
        from PIL import Image
        with Image.open(path) as im:
            mp = (im.width * im.height) / 1_000_000
            if mp > config.PREVIEW_MAX_MP:
                skip = (f"{im.width}×{im.height} ({mp:.0f} MP) — preview skipped "
                        f"to spare the render")
            else:
                # draft() lets the JPEG decoder work at 1/2, 1/4 or 1/8 scale,
                # so a large JPEG never gets fully decoded. No-op for PNG/WebP.
                im.draft("RGB", (max_px, max_px))
                im = im.convert("RGB")
                im.thumbnail((max_px, max_px), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=86)
                data = buf.getvalue()
    except Exception as e:
        return None, f"unreadable ({type(e).__name__})"

    with _thumb_lock:
        _thumb_cache["key"] = key
        _thumb_cache["bytes"] = data
        _thumb_cache["skip"] = skip
    return data, skip


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

    def _is_local(self):
        """
        True when the browser is on this machine.

        Judged by the Host header, not the client IP: tunnel traffic arrives
        from cloudflared on 127.0.0.1 too, so the source address cannot tell
        local from remote. The hostname can.
        """
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "::1", "")

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
                global _last_seen
                _last_seen = time.time()      # a window is watching
                st = build_status()
                # Quitting from the phone would be a one-way door: the server
                # would stop, and nothing can start it remotely (boot_check
                # only runs at logon, and only for an interrupted render). So
                # the UI only offers Quit when you are at the machine.
                st["local"] = self._is_local()
                return self._json(st)
            if path == "/api/log":
                n = int(parse_qs(u.query).get("n", ["400"])[0])
                return self._json({"lines": controller.runner.log_tail(n)})
            if path == "/api/browse":
                # Local only. Walking the whole filesystem is a reasonable
                # convenience at the machine, and needless exposure from the
                # phone — where picking a file was never the point.
                if not self._is_local():
                    return self._json(
                        {"error": "File browsing is only available on the "
                                  "render PC."}, 403)
                p = parse_qs(u.query).get("path", [""])[0]
                return self._json(browse.listing(p))
            if path == "/api/blendinfo":
                # Local only: it runs Blender and reads a path off this disk.
                if not self._is_local():
                    return self._json({"error": "Local only."}, 403)
                import blend_info
                q = parse_qs(u.query)
                return self._json(blend_info.read(
                    q.get("path", [""])[0],
                    refresh=q.get("refresh", ["0"])[0] == "1"))
            if path == "/api/gridpreview":
                if not self._is_local():
                    return self._json({"error": "Local only."}, 403)
                import blend_info
                import grid_preview
                q = parse_qs(u.query)
                blend = q.get("blend", [""])[0]
                script = q.get("script", [""])[0] or config.DEFAULT_TILED_SCRIPT
                return self._json(grid_preview.preview(
                    script, blend_info.read(blend),
                    int(q.get("long_edge", ["120000"])[0]),
                    int(q.get("tile_long", ["4096"])[0])))
            if path == "/api/tile":
                return self._tile()
            if path == "/sw.js":
                # Served from the ROOT, not /static/. A service worker's scope
                # is its own directory, so one at /static/sw.js cannot control
                # the app at "/" — and Chrome will not offer "Install app"
                # when the worker does not control start_url.
                return self._static("sw.js", ctype="text/javascript")
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
                return self._json({"stopped": controller.stop(
                    after_current=bool(body.get("after_current")))})
            if path == "/api/cancel_stop":
                return self._json({"cancelled": controller.cancel_stop_after()})
            if path == "/api/start":
                job = (controller.start(body["job_id"]) if body.get("job_id")
                       else controller.start_next())
                return self._json({"started": job["id"] if job else None})
            if path == "/api/resume":
                return self._json({"job": qm.resume(body["job_id"])})
            if path == "/api/enqueue":
                # Set a stopped job waiting, in place. Reachable from the phone
                # too: lining up what runs next is exactly the kind of thing
                # worth doing from bed while a render is going.
                return self._json({"job": qm.enqueue(body["job_id"])})
            if path == "/api/move":
                return self._json({"order": qm.move(body["job_id"],
                                                    int(body["offset"]))})
            if path == "/api/remove":
                qm.remove(body["job_id"])
                return self._json({"removed": body["job_id"]})
            if path == "/api/quit":
                # Local only: shutting the server down from the phone is a
                # one-way door, since nothing can start it again remotely.
                if not self._is_local():
                    return self._json(
                        {"error": "Rendafar can only be closed on the render PC."},
                        403)
                return self._quit(force=bool(body.get("force")))
            if path == "/api/pick":
                if not self._is_local():
                    return self._json(
                        {"error": "The file dialog opens on the render PC."}, 403)
                return self._pick_file(body)
            if path == "/api/edit":
                if not self._is_local():
                    return self._json({"error": "Local only."}, 403)
                return self._edit_job(body)
            if path == "/api/new":
                # Local only, deliberately. Remote is for watching and steering
                # renders that already exist — never for creating them.
                #
                # This is defence in depth. Cloudflare Access already means
                # only Tiphaine gets here, but creating a job names an
                # arbitrary file for Blender to open, and a .blend can carry
                # auto-executing Python. Nothing is gained by offering that
                # over the internet when adding renders from afar was never
                # wanted.
                if not self._is_local():
                    return self._json(
                        {"error": "Renders can only be added on the render PC."},
                        403)
                return self._new_job(body)
            return self._send(404, "not found")
        except (KeyError, ValueError, RuntimeError, FileNotFoundError) as e:
            return self._json({"error": str(e)}, 400)
        except BrokenPipeError:
            pass
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # -- handlers --

    def _pick_file(self, body):
        """
        Open a real Windows file dialog on the render PC.

        Local only, and not merely for safety: the dialog appears on the
        machine's screen, so triggering it from the phone would pop a window
        nobody is standing in front of.

        Run as a separate process — Tk is not thread-safe and the server
        answers on worker threads.
        """
        py = sys.executable
        # Use the console-less interpreter when there is one, so no black
        # window flashes up behind the dialog.
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.isfile(pyw):
            py = pyw

        args = [py, os.path.join(config.APP_DIR, "pick_file.py")]
        start = (body.get("start") or "").strip()
        if start:
            args.append(start)
        try:
            # Generous timeout: this waits on a human browsing their disk.
            out = subprocess.run(
                args, capture_output=True, text=True, timeout=600,
                cwd=config.APP_DIR,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return self._json({"path": "", "error": "the dialog timed out"})
        except OSError as e:
            return self._json({"path": "", "error": str(e)})

        path = (out.stdout or "").strip()
        if not path:
            return self._json({"path": ""})          # cancelled — not an error
        return self._json({"path": path})

    # Settings a job will accept after creation, per type. Anything absent is
    # absent because the render script has no override for it, or because
    # changing it would invalidate work already on disk.
    EDITABLE = {
        # OUTPUT_DIR is safe to change here only because the guard below
        # refuses once anything has been rendered; moving the target folder
        # mid-render would abandon the tiles already written.
        "tiled":   {"FILE_FORMAT", "RENDER_NAME", "OUTPUT_DIR"},
        # FILE_FORMAT needs a script that has the override — snapshots from
        # before 2026-07-27 do not, so it is checked per job below rather than
        # assumed from the type.
        "classic": {"CAMERA", "FRAME", "FRAME_START", "FRAME_END", "FRAME_STEP",
                    "OUTPUT_DIR", "DEVICE", "FILE_FORMAT"},
    }

    def _edit_job(self, body):
        """
        Change a queued job's settings.

        Only while it is queued and has produced nothing. Once tiles or frames
        exist, changing the camera or the frame range would mix two different
        renders in one folder — the kind of mistake only noticed at the end.
        """
        job = qm.load_job(body.get("job_id") or "")
        if job is None:
            raise KeyError(body.get("job_id"))
        if job["state"] != qm.QUEUED:
            raise ValueError(f"{job['id']} is {job['state']} — only a queued "
                             f"job can be changed")

        prog = paths.progress(job)
        if prog and prog.get("done"):
            raise ValueError(
                f"{prog['done']} {prog['unit']} already rendered — changing "
                f"settings now would mix two renders in one folder. Create a "
                f"new job instead.")

        allowed = set(self.EDITABLE.get(job.get("type"), set()))

        # Narrow it to what this job's pinned script actually understands. A
        # job created before a setting existed keeps the older snapshot, and
        # would otherwise be handed a key its script rejects at launch.
        supported = paths.script_overridable(job.get("script", ""))
        if supported is not None:
            allowed &= supported
        changes = dict(body.get("config") or {})

        # "_OUTPUT" is the folder as the UI shows it. Translating it here means
        # the split lives in one place and every front end gets it right.
        if "_OUTPUT" in changes:
            changes.update(paths.split_output(changes.pop("_OUTPUT"),
                                              job.get("type")))
        for key in changes:
            if key not in allowed:
                raise ValueError(
                    f"{key} cannot be changed on a {job.get('type')} job. "
                    f"Allowed: {', '.join(sorted(allowed)) or 'nothing'}")

        cfg = dict(job.get("config") or {})
        for key, value in changes.items():
            if value in ("", None):
                cfg.pop(key, None)          # empty means "back to the .blend"
            else:
                cfg[key] = value
        job["config"] = cfg
        qm.save_job(job)
        return self._json({"job": job})

    def _new_job(self, body):
        """
        Create a job from the UI.

        Only whitelisted settings are accepted, and they are handed to the same
        create_job() the CLI uses, so the render script's own validation still
        applies. Everything else — resolution, samples, output path — continues
        to come from the .blend.
        """
        blend = (body.get("blend") or "").strip().strip('"')
        if not blend.lower().endswith(".blend"):
            raise ValueError("not a .blend file")
        if not os.path.isfile(blend):
            raise FileNotFoundError(f"file not found: {blend}")

        kind = body.get("kind", "tiled")
        cfg  = {}
        if kind == "tiled":
            cfg["LONG_EDGE"]   = int(body.get("long_edge") or 120000)
            cfg["TILE_LONG"]   = int(body.get("tile_long") or 4096)
            cfg["FILE_FORMAT"] = (body.get("format") or "WEBP").upper()
            if body.get("render_name"):
                cfg["RENDER_NAME"] = str(body["render_name"])
            if body.get("preview"):
                cfg.update({"PREVIEW_MODE": True, "PREVIEW_GRID": 5})
            job_type = "tiled"
        else:
            job_type = "classic"
            cfg["MODE"] = "still" if kind == "still" else "animation"
            if kind == "still" and body.get("frame"):
                cfg["FRAME"] = int(body["frame"])
            if kind == "animation":
                if body.get("frame_start"):
                    cfg["FRAME_START"] = int(body["frame_start"])
                if body.get("frame_end"):
                    cfg["FRAME_END"] = int(body["frame_end"])

        job = qm.create_job(blend, name=(body.get("name") or None),
                            job_type=job_type, cfg=cfg)

        out = paths.job_output_dir(job)
        warn = None
        if out and os.path.isdir(out):
            n = len([f for f in os.listdir(out) if f.startswith("tile_")])
            if n:
                # Same folder as an earlier render: this RESUMES into it rather
                # than starting fresh, which is a trap when a new render was
                # intended. Surfaced, never silent.
                warn = (f"{n} tiles already exist in {out} — this job will "
                        f"resume into that folder. Use a different render name "
                        f"for a separate render.")
        return self._json({"job": job, "output_dir": out, "warning": warn})

    def _quit(self, force=False):
        """
        Shut Rendafar down.

        Launched from the desktop icon there is no console to close, so this is
        the way out. It refuses while a render is running: killing the
        supervisor would leave Blender rendering with nothing watching it,
        nothing to finalise the job, and nothing to start the next one — the
        orphan situation that cost us a debugging session in Phase 3.

        `force` is the deliberate path: stop the render first, then quit.
        """
        if controller.runner.is_busy():
            if not force:
                job = controller.runner.status().get("job") or {}
                return self._json({
                    "quit": False,
                    "rendering": True,
                    "job": job.get("name"),
                    "error": "A render is running. Quitting would leave it "
                             "unsupervised.",
                }, 409)
            controller.stop()

        self._json({"quit": True})

        def _shutdown():
            # From another thread: serve_forever() cannot be stopped from
            # inside the request currently being handled.
            self.server.shutdown()
            # server_close() releases the listening socket. Without it the port
            # stays bound until the process exits, and relaunching from the
            # desktop icon would fail to bind.
            self.server.server_close()

        threading.Thread(target=_shutdown, daemon=True).start()

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
        data, skip = thumbnail(lt[0], lt[1])
        if not data:
            # 415 rather than 404: the image exists, it is just too expensive
            # to preview. The UI shows the reason instead of an empty frame.
            return self._send(415, skip or "unreadable")
        return self._send(200, data, "image/jpeg")

    def _static(self, rel, ctype=None):
        rel  = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            return self._send(404, "not found")
        ctype = ctype or mimetypes.guess_type(full)[0] or "application/octet-stream"
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

    # First-tile photo and periodic "how's it going" updates. Unconditional —
    # unlike the idle watchdog above, this has nothing to do with whether a
    # window is open. The whole point of Telegram is finding out with nobody
    # watching, so it runs whenever the server does, web-only sessions included.
    if notify.enabled():
        import notify_watch
        threading.Thread(target=notify_watch.loop, daemon=True).start()

    # Localhost only. Remote access arrives through cloudflared, which runs on
    # this machine and connects over 127.0.0.1 — the app is never on the LAN.
    srv = ThreadingHTTPServer((config.HOST, PORT), Handler)
    print(f"\n  Rendafar  →  http://127.0.0.1:{PORT}")
    print(f"  remote          →  via Cloudflare Tunnel (see PLAN.md)")

    # Only in app mode: started from a console (or by the power-cut auto-resume)
    # it stays up until you close the window yourself.
    if os.environ.get("RENDAFAR_APP_MODE") == "1" and config.IDLE_QUIT_S > 0:
        print(f"  app mode        :  closes {config.IDLE_QUIT_S}s after the last "
              f"window, unless rendering")
        threading.Thread(target=idle_watchdog, args=(srv,), daemon=True).start()
    print(f"  machine         :  {socket.gethostname()}")
    print(f"  queue           :  {config.QUEUE_DIR}")
    print("  Ctrl+C to stop the server (renders keep running)\n")
    try:
        srv.serve_forever()
        print("\n  Rendafar stopped.")
    except KeyboardInterrupt:
        print("\n  server stopped — any running render continues")


if __name__ == "__main__":
    main()
