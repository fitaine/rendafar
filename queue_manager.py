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
    n = 0
    if os.path.isdir(config.QUEUE_DIR):
        for fn in os.listdir(config.QUEUE_DIR):
            m = JOB_RE.match(fn)
            if m:
                n = max(n, int(m.group(1)))
    return f"job_{n + 1:04d}"


def create_job(blend, name=None, job_type="tiled", cfg=None, script=None):
    """
    Create a job. `blend` is used in place — never copied or moved.

    `script` pins the render script for the life of the job, including every
    resume.  It defaults to the current frozen snapshot so a later edit to the
    master script can never change how an existing job renders.
    """
    blend = os.path.abspath(blend)
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
    st = load_state()
    st["order"] = [i for i in st["order"] if i != job_id]
    save_state(st)
