"""
Talking to the Rendafar server.

The desktop app is a CLIENT, not the engine. server.py keeps owning the queue
and Blender, which is what lets a render survive this window closing, keeps the
phone working while the desktop app is shut, and keeps power-cut recovery
independent of Qt.

Stdlib HTTP only — the server is stdlib too, and this is one localhost request
every couple of seconds.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import health

BASE = f"http://127.0.0.1:{config.PORT}"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ServerDown(Exception):
    """The server is not answering. Not fatal: the UI says so and keeps trying."""


# ── Process ──────────────────────────────────────────────────────────────────

def port_open(timeout=1.5):
    """
    True only when Rendafar answers on the port — not merely something.

    This used to be a bare TCP connect. A foreign server on 8765 then passed
    for ours, ensure_server() skipped starting server.py, and every call here
    went to the stranger. See health.py.
    """
    return health.server_alive(timeout)


def start_server():
    """
    Launch server.py hidden.

    RENDAFAR_APP_MODE is set as a safety net, not as the main mechanism:
    closing the window shuts the server down explicitly (MainWindow.closeEvent).
    But if this app crashes, nothing would be left to do that, and a hidden
    server would linger for ever. In app mode the server closes itself once
    nothing has polled for a while and nothing is rendering — a render, or a
    phone still watching, always keeps it alive.
    """
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    env = os.environ.copy()
    env["RENDAFAR_APP_MODE"] = "1"
    subprocess.Popen(
        [pyw, os.path.join(config.APP_DIR, "server.py")],
        cwd=config.APP_DIR, env=env,
        creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )


def ensure_server(timeout=25):
    """Start the server if it is not already up. True once it answers."""
    if port_open():
        return True
    start_server()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open():
            return True
        time.sleep(0.3)
    return False


# ── Requests ─────────────────────────────────────────────────────────────────

def _call(path, payload=None, timeout=20):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        # The server explains refusals in the body; surface that, not a code.
        try:
            return json.loads(e.read() or b"{}")
        except ValueError:
            return {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        raise ServerDown(str(e))


def status():
    return _call("/api/status", timeout=8)


def log(n=400):
    return _call(f"/api/log?n={n}", timeout=8).get("lines", [])


def start(job_id=None):
    return _call("/api/start", {"job_id": job_id} if job_id else {})


def stop(after_current=False):
    return _call("/api/stop", {"after_current": bool(after_current)})


def cancel_stop():
    """Change your mind about a 'stop after this tile' and carry on."""
    return _call("/api/cancel_stop", {})


def resume(job_id):
    return _call("/api/resume", {"job_id": job_id})


def duplicate(job_id):
    """Copy a job, queued, directly below the original."""
    return _call("/api/duplicate", {"job_id": job_id})


def unqueue(job_id):
    """Take a queued job back out of the queue, without losing its progress."""
    return _call("/api/unqueue", {"job_id": job_id})


def enqueue(job_id):
    """Set a stopped job waiting in the queue, in place, without starting it."""
    return _call("/api/enqueue", {"job_id": job_id})


def reorder(order):
    """Set the whole queue order — what a drag produces."""
    return _call("/api/reorder", {"order": list(order)})


def move(job_ids, offset):
    """Move one job or a whole selection. Accepts an id or a list of them."""
    if isinstance(job_ids, str):
        job_ids = [job_ids]
    return _call("/api/move", {"job_ids": list(job_ids), "offset": offset})


def remove(job_id):
    return _call("/api/remove", {"job_id": job_id})


def new_job(body):
    return _call("/api/new", body, timeout=30)


def edit_job(job_id, config):
    """Change a queued job's settings. The server enforces what is allowed."""
    return _call("/api/edit", {"job_id": job_id, "config": config})


def quit_server(force=False):
    return _call("/api/quit", {"force": force})


def blend_info(path, refresh=False):
    """
    The .blend's own settings. Slow the first time (Blender has to open the
    file), instant afterwards — the server caches by path and mtime.
    """
    q = urllib.parse.urlencode({"path": path, "refresh": "1" if refresh else "0"})
    return _call(f"/api/blendinfo?{q}", timeout=200)


def grid_preview(blend, long_edge, tile_long, script=""):
    """
    What a tiled render would actually produce. Computed by the pinned render
    script's own compute_grid(), so the preview cannot drift from the render.
    """
    q = urllib.parse.urlencode({"blend": blend, "long_edge": long_edge,
                                "tile_long": tile_long, "script": script})
    return _call(f"/api/gridpreview?{q}", timeout=200)


def tile_bytes():
    """Raw JPEG of the newest rendered image, or None."""
    try:
        with urllib.request.urlopen(BASE + f"/api/tile?t={time.time()}",
                                    timeout=15) as r:
            return r.read()
    except Exception:
        return None
