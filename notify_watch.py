"""
Telegram updates that need nobody watching.

The lifecycle messages in notify.py fire from inside controller.py, which only
runs when something calls start/stop/resume. This is different: a first-tile
photo and a periodic "how's it going" have to appear whether or not a browser
or the desktop app is even open, because that is the entire point of a phone
notification. So this is a loop the server runs on its own, started once from
server.main(), independent of RENDAFAR_APP_MODE.

It reads exactly the files everything else already reads — state.json and the
running job's manifest — so it adds no coupling to the runner or the poller.
"""

import time

import config
import notify
import queue_manager as qm

POLL_S = 20

# job_id -> {"baseline_done": int, "first_tile_sent": bool, "last_periodic": float}
_seen = {}


def _thumbnail_for(job):
    """
    The latest tile/frame as JPEG bytes, or None.

    Deferred import: server.py defines `thumbnail()` (with its own cache and
    size guard) and, at startup, imports this module to launch the loop. A
    top-level `import server` here would be circular; importing inside the
    function runs long after both modules have finished loading, so it just
    works. It also means test_phase7.py's direct calls to `server_mod.thumbnail`
    keep working unchanged — the function still lives in exactly one place.
    """
    import paths
    lt = paths.latest_tile(job)
    if not lt:
        return None
    import server
    data, _skip = server.thumbnail(lt[0], lt[1])
    return data


def _eta_clock(prog):
    """paths.format_eta_clock for this progress dict, or "" — imported lazily
    to match notify.py's rule that the notifier never hard-depends on the rest
    of the app at import time."""
    try:
        import paths
        return paths.format_eta_clock(prog.get("eta_at"))
    except Exception:
        return ""


def _caption(job, prog, heading):
    unit = prog.get("unit", "tiles")
    lines = [
        heading,
        f"{prog['done']}/{prog['total']} {unit}  ({prog['percent']}%)",
    ]
    bits = []
    if prog.get("avg_tile_s"):
        bits.append(f"{notify.hms(prog['avg_tile_s'])}/{unit[:-1]}")
    if prog.get("eta_s") is not None:
        bits.append(f"ETA {notify.hms(prog['eta_s'])}")
    if bits:
        lines.append("  ·  ".join(bits))
    # On a phone, hours away from the machine, the wall-clock finish is the
    # part worth reading — a bare "ETA 63h 20m" still needs working out.
    clock = _eta_clock(prog)
    if clock:
        lines.append(f"should finish {clock}")

    try:
        import gpu
        g = gpu.read(rendering=True, avg_tile_s=prog.get("avg_tile_s"))
        if g.get("available"):
            lines.append(f"GPU {round(g['power_w'])}/{round(g['power_limit'])} W  "
                        f"{round(g['temp_c'])}°C")
    except Exception:
        pass

    return "\n".join(lines)[:1000]         # Telegram caption cap is 1024


def _tick():
    if not notify.enabled():
        return          # no point building a thumbnail nobody will receive
    st = qm.load_state()
    running_id = st.get("running")

    # Drop trackers for anything no longer the running job — a stopped job
    # resuming later is not "the same session" for first-tile purposes.
    for jid in list(_seen):
        if jid != running_id:
            del _seen[jid]

    if not running_id:
        return
    job = qm.load_job(running_id)
    if not job:
        return
    import paths
    prog = paths.progress(job)
    if not prog or not prog.get("total"):
        return

    done = prog.get("done") or 0

    if running_id not in _seen:
        # First time this loop notices the job. If it already has tiles done,
        # this is a resume or the server restarting mid-render — not the
        # actual first tile, and faking that would be exactly the kind of lie
        # this app exists to avoid. Suppress it and only start the periodic
        # clock from here.
        _seen[running_id] = {
            "baseline_done": done,
            "first_tile_sent": done > 0,
            "last_periodic": time.time(),
        }

    s = _seen[running_id]

    if config.NOTIFY_FIRST_TILE and not s["first_tile_sent"] and done >= 1:
        notify.send_photo(_thumbnail_for(job),
                          _caption(job, prog, f"📸 First tile — {job['name']}"))
        s["first_tile_sent"] = True
        s["last_periodic"] = time.time()   # don't immediately double-send
        return

    if (config.NOTIFY_PERIODIC_S
            and time.time() - s["last_periodic"] >= config.NOTIFY_PERIODIC_S):
        notify.send_photo(_thumbnail_for(job),
                          _caption(job, prog, f"⏱ Still going — {job['name']}"))
        s["last_periodic"] = time.time()


def loop():
    while True:
        time.sleep(POLL_S)
        try:
            _tick()
        except Exception as e:
            print(f"  notify watchdog: {e}")
