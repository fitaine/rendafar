"""
The job queue.

State lives in JSON files on disk, never in memory only:

    queue/job_0007.json   one file per job
    queue/state.json      queue order + which job is running

That is deliberate.  After a power cut the question "what was I doing" must be
answerable by reading the disk, not by trusting something the app remembered.
"""

import datetime
import json
import os
import re
import tempfile

import config

# ── States ───────────────────────────────────────────────────────────────────
#
#   queued ──▶ running ──▶ done
#      ▲          │
#      │          ├──▶ stopped      user pressed Stop
#      │          ├──▶ failed       blender exited badly / render incomplete
#      │          └──▶ interrupted  found 'running' at startup with no process
#      └──────────┴─ resume() puts it back to queued, at the front

QUEUED, RUNNING, DONE, STOPPED, FAILED, INTERRUPTED = (
    "queued", "running", "done", "stopped", "failed", "interrupted")

RESUMABLE = (STOPPED, FAILED, INTERRUPTED)
FINISHED  = (DONE,)

JOB_RE = re.compile(r"^job_(\d+)\.json$")


# ── Atomic JSON ──────────────────────────────────────────────────────────────

def _write_json(path, data):
    """
    Write JSON atomically.

    A half-written job file after a power cut would be worse than no file at
    all, so write to a temp file in the same directory and os.replace() it in —
    replace is atomic on Windows for same-volume paths.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _now():
    return (datetime.datetime.now(datetime.timezone.utc)
            .astimezone().isoformat(timespec="seconds"))


# ── Queue state ──────────────────────────────────────────────────────────────

def load_state():
    st = _read_json(config.STATE_FILE, {}) or {}
    st.setdefault("order", [])
    st.setdefault("running", None)
    return st


def save_state(st):
    _write_json(config.STATE_FILE, st)


# ── Jobs ─────────────────────────────────────────────────────────────────────

def job_path(job_id):
    return os.path.join(config.QUEUE_DIR, f"{job_id}.json")


def load_job(job_id):
    return _read_json(job_path(job_id))


def save_job(job):
    _write_json(job_path(job["id"]), job)
    return job


def _next_id():
    """
    A job id that has never been used before.

    Ids must NOT be recycled. A job's manifest is named after its id, so a
    reused id inherits the dead job's manifest: a brand new render then starts
    with someone else's frame times and reports an ETA computed from a
    different scene entirely. That happened here — a fresh still of one scene
    showed 0/1 frames done and an ETA of 984s, carried over from a deleted
    Ferme-des-Crozets camera.

    Counting the job files alone is not enough, because removing the newest job
    lowers the maximum and the next one takes its number back. A high-water
    mark in state.json settles it, with the files as a floor so an old queue
    that predates the mark still behaves.
    """
    n = 0
    if os.path.isdir(config.QUEUE_DIR):
        for fn in os.listdir(config.QUEUE_DIR):
            m = JOB_RE.match(fn)
            if m:
                n = max(n, int(m.group(1)))
            # Orphan manifests count too: they are exactly the collision.
            elif fn.startswith("job_") and fn.endswith("_manifest.json"):
                try:
                    n = max(n, int(fn[4:8]))
                except ValueError:
                    pass

    st = load_state()
    n = max(n, int(st.get("last_id_n") or 0))
    st["last_id_n"] = n + 1
    save_state(st)
    return f"job_{n + 1:04d}"


def create_job(blend, name=None, job_type="tiled", cfg=None, script=None):
    """
    Create a job. `blend` is used in place — never copied or moved.

    `script` pins the render script for the life of the job, including every
    resume.  It defaults to the current frozen snapshot so a later edit to the
    master script can never change how an existing job renders.
    """
    import paths            # local, as elsewhere in this module
    blend = os.path.abspath(paths.clean_path(blend))
    if not os.path.isfile(blend):
        raise FileNotFoundError(f"blend file not found: {blend}")

    config.ensure_dirs()
    if job_type not in config.DEFAULT_SCRIPTS:
        raise ValueError(f"unknown job type {job_type!r} — "
                         f"expected one of {sorted(config.DEFAULT_SCRIPTS)}")

    job = {
        "id":         _next_id(),
        "type":       job_type,
        "name":       name or os.path.basename(blend).replace(".blend", ""),
        "blend":      blend,
        "script":     os.path.abspath(
                          script or config.DEFAULT_SCRIPTS[job_type]),
        "state":      QUEUED,
        "created_at": _now(),
        "config":     cfg or {},
        "history":    [],
    }
    save_job(job)

    st = load_state()
    st["order"].append(job["id"])
    save_state(st)
    return job


def duplicate(job_id):
    """
    Copy a job, queued, directly below the original.

    For rendering the same scene several ways — a different camera per copy —
    without picking the .blend again and waiting on another probe of a
    multi-gigabyte file.

    The copy keeps the original's PINNED SCRIPT rather than taking today's
    snapshot. A duplicate is the same render with one setting changed, so it
    has to be rendered by the same bytes; silently upgrading it would be the
    one thing the pinning exists to prevent.

    History and timestamps are NOT copied: the copy has rendered nothing, and
    inheriting "stopped at 691/900" would be a lie about work that belongs to
    the original.
    """
    src = load_job(job_id)
    if src is None:
        raise KeyError(job_id)

    job = {
        "id":         _next_id(),
        "type":       src["type"],
        "name":       _copy_name(src.get("name", "")),
        "blend":      src["blend"],
        "script":     src["script"],
        "state":      QUEUED,
        "created_at": _now(),
        "config":     dict(src.get("config") or {}),
        "history":    [],
    }

    # Two tiled jobs on one .blend with the same RENDER_NAME write into the
    # same folder: the copy would find the original's tiles, skip every one of
    # them and "finish" in seconds having rendered nothing. RENDER_NAME
    # defaults to the .blend's own name, so a plain copy collides by default —
    # give it its own folder up front rather than leaving a trap to walk into.
    if job["type"] == "tiled":
        base = (job["config"].get("RENDER_NAME")
                or os.path.basename(src["blend"]).replace(".blend", ""))
        job["config"]["RENDER_NAME"] = _unique_render_name(base)

    save_job(job)

    # Directly below the original, not at the end: copies of one scene belong
    # together, and the whole point is usually to run them one after another.
    st = load_state()
    order = st["order"]
    st["order"] = (order[:order.index(job_id) + 1] + [job["id"]]
                   + order[order.index(job_id) + 1:]) if job_id in order \
        else order + [job["id"]]
    save_state(st)
    return job


def _next_free(name, taken):
    """'Bure' -> 'Bure (2)' -> 'Bure (3)', skipping anything already in use."""
    m = re.match(r"^(.*) \((\d+)\)$", name)
    base, n = (m.group(1), int(m.group(2)) + 1) if m else (name, 2)
    while f"{base} ({n})" in taken:
        n += 1
    return f"{base} ({n})"


def _copy_name(name):
    return _next_free(name, {j.get("name") for j in all_jobs()})


def _unique_render_name(base):
    """A RENDER_NAME no existing job is already writing into."""
    taken = set()
    for j in all_jobs():
        cfg = j.get("config") or {}
        taken.add(cfg.get("RENDER_NAME")
                  or os.path.basename(j.get("blend", "")).replace(".blend", ""))
    return _next_free(base, taken)


def set_state(job_id, state, note=None):
    job = load_job(job_id)
    if job is None:
        raise KeyError(job_id)
    job["state"] = state
    job.setdefault("history", []).append(
        {"at": _now(), "state": state, **({"note": note} if note else {})})
    if state == RUNNING:
        job["last_started_at"] = _now()
    if state in FINISHED + RESUMABLE:
        job["last_ended_at"] = _now()
    return save_job(job)


def all_jobs():
    """Every job, in queue order (unknown ids appended by id)."""
    if not os.path.isdir(config.QUEUE_DIR):
        return []
    ids = [JOB_RE.match(fn).group(0)[:-5]
           for fn in os.listdir(config.QUEUE_DIR) if JOB_RE.match(fn)]
    order = load_state()["order"]
    ranked = sorted(ids, key=lambda j: (order.index(j) if j in order else 10**6, j))
    return [j for j in (load_job(i) for i in ranked) if j]


def next_queued():
    """First job in queue order whose state is 'queued', or None."""
    for job in all_jobs():
        if job["state"] == QUEUED:
            return job
    return None


def enqueue(job_id):
    """
    Put a stopped job back in the queue to WAIT, without starting it and
    without changing where it sits in the order.

    This is the state the queue actually runs on. start_next() only ever picks
    a job whose state is literally `queued`, so a job left `stopped` is not
    waiting for anything — it is parked. Two renders finished here with other
    work sitting `stopped` behind them and nothing followed, because from the
    queue's point of view there was nothing to follow.

    Distinct from resume(), which jumps a job to the FRONT: that is right for
    power-cut recovery, where the interrupted job should come back first, and
    wrong here, where the whole point is that queue order decides.
    """
    job = load_job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job["state"] == RUNNING:
        raise ValueError(f"{job_id} is already running")
    if job["state"] == DONE:
        raise ValueError(f"{job_id} is already done")
    if job["state"] == QUEUED:
        return job                      # already waiting; nothing to do
    return set_state(job_id, QUEUED, note="queued to follow")


def unqueue(job_id):
    """
    Take a job back OUT of the queue: queued -> stopped, in place.

    The opposite of enqueue(). Stopped means parked — start_next() skips it —
    so this is how a job that is waiting its turn is set aside without being
    removed, and without losing anything it has already rendered.
    """
    job = load_job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job["state"] == RUNNING:
        raise ValueError(f"{job_id} is running — stop it instead")
    if job["state"] == DONE:
        raise ValueError(f"{job_id} is already done")
    if job["state"] != QUEUED:
        return job                       # already parked; nothing to do
    return set_state(job_id, STOPPED, note="taken out of the queue")


def resume(job_id):
    """
    Put a stopped / failed / interrupted job back at the front of the queue.

    The job's `script` field is deliberately left untouched: a resume must run
    the exact bytes the render started with, or tiles rendered before and after
    could disagree about framing.
    """
    job = load_job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job["state"] == RUNNING:
        raise ValueError(f"{job_id} is already running")
    if job["state"] == DONE:
        raise ValueError(f"{job_id} is already done")

    st = load_state()
    order = [i for i in st["order"] if i != job_id]
    st["order"] = [job_id] + order
    save_state(st)
    return set_state(job_id, QUEUED, note="resumed")


def move(job_id, offset):
    """Move a job up (-1) or down (+1) the queue."""
    st = load_state()
    order = st["order"]
    if job_id not in order:
        raise KeyError(job_id)
    i = order.index(job_id)
    j = max(0, min(len(order) - 1, i + offset))
    order.insert(j, order.pop(i))
    st["order"] = order
    save_state(st)
    return order


def move_many(job_ids, offset):
    """
    Move several jobs one step up (-1) or down (+1), together.

    Not a loop over move(): moving each in turn makes them leapfrog each other
    and scrambles their order. Rows are taken in the direction of travel — top
    first going up, bottom first going down — and one that has nowhere to go
    blocks the next, so a selection stacks against the end of the queue instead
    of collapsing into it.

    The relative order of the moved rows is always preserved, which is the
    whole reason for selecting several at once.
    """
    st = load_state()
    order = list(st["order"])
    picked = [i for i in job_ids if i in order]
    if not picked or offset == 0:
        return order

    idx = sorted(order.index(i) for i in picked)
    if offset < 0:
        blocked = -1                    # the last index that cannot move up
        for pos in idx:                 # top first
            if pos - 1 <= blocked:
                blocked = pos           # sitting against the top, or a stuck row
                continue
            order[pos - 1], order[pos] = order[pos], order[pos - 1]
            blocked = pos - 1
    else:
        blocked = len(order)            # the first index that cannot move down
        for pos in reversed(idx):       # bottom first
            if pos + 1 >= blocked:
                blocked = pos
                continue
            order[pos + 1], order[pos] = order[pos], order[pos + 1]
            blocked = pos + 1

    st["order"] = order
    save_state(st)
    return order


def reorder(ids):
    """Set the full queue order (ids not listed keep their relative order)."""
    st = load_state()
    rest = [i for i in st["order"] if i not in ids]
    st["order"] = list(ids) + rest
    save_state(st)
    return st["order"]


def remove(job_id):
    """Delete a job from the queue. Never touches render output on disk."""
    job = load_job(job_id)
    if job and job["state"] == RUNNING:
        raise ValueError(f"{job_id} is running — stop it first")
    try:
        os.unlink(job_path(job_id))
    except FileNotFoundError:
        pass

    # The job's manifest goes with it. Left behind, it is inherited by whatever
    # job takes that id next, which then starts life with another render's
    # frame times, ETA and — because the script carries last_file forward —
    # another render's image in the preview. That is exactly what happened
    # here: a fresh still showed a finished Ferme-des-Crozets frame.
    #
    # This is only the tidying half. Ids are no longer recycled either, so a
    # manifest that survives some other way still cannot be picked up.
    try:
        import paths
        os.unlink(paths.classic_manifest_path({"id": job_id}))
    except (FileNotFoundError, OSError):
        pass
    st = load_state()
    st["order"] = [i for i in st["order"] if i != job_id]
    save_state(st)
