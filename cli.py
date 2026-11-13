"""
Command line for the queue — exercises everything the web UI will do later.

    python cli.py add "C:\\...\\scene.blend" --name "Ecrins 120k" --long-edge 120000
    python cli.py add "C:\\...\\scene.blend" --preview          # 5x5 preview grid
    python cli.py list
    python cli.py start            # start next queued job
    python cli.py start job_0003   # start one specific job now
    python cli.py stop
    python cli.py status
    python cli.py log  [job_0003] [-n 60]
    python cli.py resume job_0003
    python cli.py up|down job_0003
    python cli.py rm job_0003
    python cli.py recover          # what would happen after a power cut
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import config
import paths
import queue_manager as qm
from controller import Controller

BASE = f"http://127.0.0.1:{config.PORT}"


# ── Talking to the server ────────────────────────────────────────────────────
#
# Only ONE process may supervise a render.  server.py owns the queue whenever
# it is up: it holds the threads that capture Blender's output and finalise the
# job when it exits.  A CLI that started its own render would launch Blender,
# then exit and take those threads with it — leaving the job stuck in "running"
# for ever with an orphaned Blender behind it.
#
# So: if the server is up, the CLI is a thin client over HTTP.  If it is not,
# the CLI supervises the render itself and must stay in the foreground.

def server_up():
    try:
        urllib.request.urlopen(f"{BASE}/api/status", timeout=1.5).read()
        return True
    except (urllib.error.URLError, OSError):
        return False


def api(path, payload=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{"error":"request failed"}')

SYMBOL = {
    qm.QUEUED: "·", qm.RUNNING: "▶", qm.DONE: "✓",
    qm.STOPPED: "■", qm.FAILED: "✗", qm.INTERRUPTED: "⚡",
}


def _hms(s):
    if s is None:
        return "—"
    s = int(s)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def cmd_add(a):
    cfg = {}
    if a.long_edge:
        cfg["LONG_EDGE"] = a.long_edge
    if a.tile:
        cfg["TILE_LONG"] = a.tile
    if a.format:
        cfg["FILE_FORMAT"] = a.format
    if a.preview:
        cfg.update({"PREVIEW_MODE": True, "PREVIEW_GRID": a.preview_grid,
                    "PREVIEW_SAMPLES": a.preview_samples})
    job = qm.create_job(a.blend, name=a.name, cfg=cfg)
    print(f"  {job['id']}  {job['name']}")
    print(f"      blend  : {job['blend']}")
    print(f"      script : {job['script']}   (pinned)")
    print(f"      output : {paths.job_output_dir(job)}")
    print(f"      config : {job['config'] or 'CONFIG block defaults'}")


def cmd_classic(a):
    """
    Queue an ordinary render. Everything not named here — resolution, samples,
    output path, format — comes from the .blend, untouched.
    """
    cfg = {"MODE": "still" if a.still is not None or a.mode == "still"
                   else "animation"}
    if a.still is not None:
        cfg["FRAME"] = a.still
    if a.start:
        cfg["FRAME_START"] = a.start
    if a.end:
        cfg["FRAME_END"] = a.end
    if a.step:
        cfg["FRAME_STEP"] = a.step
    if a.camera:
        cfg["CAMERA"] = a.camera
    if a.output:
        cfg["OUTPUT_DIR"] = a.output
    if a.device:
        cfg["DEVICE"] = a.device
    if a.tiles:
        if cfg["MODE"] != "still":
            raise SystemExit("--tiles only applies to a still")
        cfg["TILE_SIZE"] = a.tiles

    job = qm.create_job(a.blend, name=a.name, job_type="classic", cfg=cfg)
    print(f"  {job['id']}  {job['name']}   [classic / {cfg['MODE']}]")
    print(f"      blend  : {job['blend']}")
    print(f"      script : {job['script']}   (pinned)")
    print(f"      config : {cfg}")
    print(f"      output : taken from the .blend at render time")


def cmd_list(a):
    jobs = qm.all_jobs()
    if not jobs:
        print("  queue is empty")
        return
    for job in jobs:
        p = paths.progress(job)
        prog = (f"{p['done']}/{p['total']} {p['unit']} ({p['percent']}%)"
                if p and p["total"] else "")
        print(f"  {SYMBOL.get(job['state'], '?')} {job['id']}  "
              f"{job['state']:<12} {job['name'][:38]:<38} {prog}")


def cmd_status(a):
    # The server owns the running process, so ask it when it is up; a local
    # Controller would only ever report "idle".
    st = api("/api/status") if server_up() else Controller().status()
    if not st["busy"]:
        print("  idle")
        nxt = qm.next_queued()
        print(f"  next up: {nxt['id']} — {nxt['name']}" if nxt else "  nothing queued")
        return
    job = st["job"]
    print(f"  ▶ {job['id']}  {job['name']}")
    print(f"      pid      : {st['pid']}")
    print(f"      session  : {_hms(st['session_s'])}")
    p = st.get("progress")
    if p:
        unit = p.get("unit", "tiles")
        print(f"      {unit:<9}: {p['done']}/{p['total']}  ({p['percent']}%)")
        clock = paths.format_eta_clock(p.get("eta_at"))
        print(f"      avg {unit[:-1]:<5}: {_hms(p['avg_tile_s'])}"
              f"    ETA {_hms(p['eta_s'])}"
              + (f"  ({clock})" if clock else ""))
    lt = paths.latest_tile(job)
    if lt:
        print(f"      newest   : {os.path.basename(lt[0])}  "
              f"({int(time.time() - lt[1])}s ago)")


def cmd_start(a):
    if server_up():
        d = api("/api/start", {"job_id": a.job_id} if a.job_id else {})
        if d.get("error"):
            print(f"  error: {d['error']}")
            return
        print(f"  started {d['started']} (supervised by the server)"
              if d.get("started") else "  nothing to start")
        print(f"  watch it at {BASE}")
        return

    # No server: this process supervises the render, so it must not exit while
    # Blender is running or the job would be orphaned mid-render.
    c = Controller()
    job = c.start(a.job_id) if a.job_id else c.start_next()
    if job is None:
        print("  nothing to start (queue empty or already busy)")
        return
    print(f"  started {job['id']} — {job['name']}")
    print("  server is not running, so this window is supervising the render.")
    print("  Leave it open until the render finishes.\n")
    _follow(c, block=True)


def _follow(c, block=False):
    seen = 0
    try:
        while c.runner.is_busy() or (block and not c.status()["queue_idle"]):
            lines = c.runner.log_tail(2000)
            for line in lines[seen:]:
                print("  " + line)
            seen = len(lines)
            time.sleep(1.0)
        print("\n  queue finished")
    except KeyboardInterrupt:
        # Detaching would orphan the render: this process holds the threads that
        # capture output and finalise the job.  Stop the render instead.
        print("\n  Ctrl+C — stopping the render (the in-flight tile is discarded)")
        c.stop()
        time.sleep(2)
        print("  stopped. Resume it later with:  python cli.py resume <job_id>")


def cmd_stop(a):
    # Only the process that launched Blender can stop it properly.
    if server_up():
        d = api("/api/stop", {})
        print("  stopped (in-flight tile discarded)" if d.get("stopped")
              else "  nothing running")
        return
    print("  nothing running (no server, and no render owned by this window)")


def cmd_log(a):
    job_id = a.job_id or (qm.load_state().get("running"))
    if not job_id:
        print("  no job specified and nothing running")
        return
    path = os.path.join(config.LOGS_DIR, f"{job_id}.log")
    if not os.path.isfile(path):
        print(f"  no log yet for {job_id}")
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-a.n:]:
            print("  " + line.rstrip())


def cmd_resume(a):
    print(f"  {qm.resume(a.job_id)['id']} re-queued at the front "
          f"(same pinned script)")


def cmd_move(a):
    qm.move(a.job_id, -1 if a.cmd == "up" else 1)
    cmd_list(a)


def cmd_rm(a):
    qm.remove(a.job_id)
    print(f"  {a.job_id} removed from the queue (render output untouched)")


def cmd_recover(a):
    c = Controller()
    found = c.recover()
    print(f"  interrupted jobs found: {found or 'none'}")


def main():
    config.ensure_dirs()
    p = argparse.ArgumentParser(prog="cli.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add");  a.set_defaults(fn=cmd_add)
    a.add_argument("blend")
    a.add_argument("--name")
    a.add_argument("--long-edge", type=int)
    a.add_argument("--tile", type=int)
    a.add_argument("--format", choices=["JPEG", "PNG", "WEBP"])
    a.add_argument("--preview", action="store_true")
    a.add_argument("--preview-grid", type=int, default=5)
    a.add_argument("--preview-samples", type=int, default=128)

    a = sub.add_parser("classic", help="queue an ordinary still or animation")
    a.set_defaults(fn=cmd_classic)
    a.add_argument("blend")
    a.add_argument("--name")
    a.add_argument("--mode", choices=["still", "animation"], default="animation")
    a.add_argument("--still", type=int, metavar="FRAME",
                   help="render one frame (implies --mode still)")
    a.add_argument("--start", type=int, help="first frame (default: the .blend's)")
    a.add_argument("--end", type=int, help="last frame (default: the .blend's)")
    a.add_argument("--step", type=int)
    a.add_argument("--camera", help="override the scene camera by name")
    a.add_argument("--output", help="override the output folder")
    a.add_argument("--tiles", type=int, default=0, metavar="PX",
                   help="still only: render in tiles of about PX pixels, resume-safe")
    a.add_argument("--device", choices=["GPU", "CPU"],
                   help="override the .blend's compute device")

    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    sub.add_parser("recover").set_defaults(fn=cmd_recover)

    a = sub.add_parser("start"); a.set_defaults(fn=cmd_start)
    a.add_argument("job_id", nargs="?")
    a.add_argument("--follow", "-f", action="store_true")

    a = sub.add_parser("log"); a.set_defaults(fn=cmd_log)
    a.add_argument("job_id", nargs="?")
    a.add_argument("-n", type=int, default=40)

    for name, fn in (("resume", cmd_resume), ("up", cmd_move),
                     ("down", cmd_move), ("rm", cmd_rm)):
        a = sub.add_parser(name); a.set_defaults(fn=fn)
        a.add_argument("job_id")

    args = p.parse_args()
    try:
        args.fn(args)
    except (KeyError, ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"  error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
