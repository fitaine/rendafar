"""
How long renders actually took, and what that says about the ones still queued.

Blender can be asked for an estimate, but it is made from the first sample
before adaptive sampling and denoising have shown their hand: on a measured
test it predicted 12.6s for a render that took 4.9s. Past renders of the same
scene are a far better guide, so this keeps them.

The unit is SECONDS PER FRAME, not per job, so a 300-frame animation and a
single still of the same scene inform each other and a part-finished job still
contributes.

Renders of one scene vary more than one number can express — two cameras of
Ferme-des-Crozets took 16.4 and 21.2 minutes, a 29% spread — so an estimate
carries how many samples it is built from and their range. A single past render
is a hint; five make a forecast.
"""

import json
import os
import statistics
import time

import config

FILE = "render_history.json"
KEEP = 40                       # per signature: enough to average, not a log


def _path():
    return os.path.join(config.QUEUE_DIR, FILE)


def signature(job):
    """
    What makes two renders comparable: the same scene of the same file, done
    the same way.

    Resolution and samples are not included because they come from the .blend
    and reading them would mean probing the file for every row; the same scene
    of the same .blend implies them. MODE separates a still from an animation
    frame, which are not the same kind of work even in one scene.
    """
    cfg = job.get("config") or {}
    return "|".join((
        os.path.basename(job.get("blend") or "?"),
        str(cfg.get("SCENE") or ""),
        str(cfg.get("MODE") or job.get("type") or ""),
    ))


def load():
    try:
        with open(_path(), encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data):
    config.ensure_dirs()
    tmp = _path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _path())
    except OSError:
        pass                    # history is a convenience; never break a render


def record(job, seconds_per_frame=None):
    """
    Remember how long this job's frames took.

    Read from the job's own manifest, which the render script writes after
    every frame — so a job that was stopped part way still contributes what it
    did finish.
    """
    times = []
    if seconds_per_frame is not None:
        times = [float(seconds_per_frame)]
    else:
        try:
            import paths
            m = paths.read_manifest(job) or {}
            times = [float(v) for v in (m.get("frame_times") or {}).values()
                     if isinstance(v, (int, float)) and v > 0]
        except Exception:
            return
    if not times:
        return

    data = load()
    key = signature(job)
    data.setdefault(key, [])
    data[key].extend(round(t, 2) for t in times)
    data[key] = data[key][-KEEP:]
    data["_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save(data)


def estimate(job):
    """
    {"per_frame_s", "n", "low", "high"} for this kind of render, or None.

    The median rather than the mean: one render interrupted by something else
    on the machine should not drag the forecast for all the others.
    """
    samples = load().get(signature(job)) or []
    samples = [s for s in samples if isinstance(s, (int, float)) and s > 0]
    if not samples:
        return None
    return {
        "per_frame_s": round(statistics.median(samples), 1),
        "n": len(samples),
        "low": round(min(samples), 1),
        "high": round(max(samples), 1),
    }


def frames_left(job):
    """How many frames this job still owes, from its manifest."""
    try:
        import paths
        p = paths.progress(job)
    except Exception:
        p = None
    if p and p.get("total"):
        return max(0, (p["total"] or 0) - (p["done"] or 0))
    # No manifest yet: a still is one frame, anything else is unknown.
    cfg = job.get("config") or {}
    return 1 if cfg.get("MODE") == "still" else None


def job_eta_s(job):
    """Seconds this job still needs, or None when nothing comparable is known."""
    est = estimate(job)
    left = frames_left(job)
    if not est or left is None:
        return None
    return round(est["per_frame_s"] * left)


def queue_eta_s(jobs):
    """
    Seconds for everything still waiting, or None if nothing can be estimated.

    Jobs with no history are skipped rather than guessed at, and the count of
    those is returned too — a total that quietly omits half the queue would be
    worse than no total.
    """
    total, known, unknown = 0, 0, 0
    for j in jobs:
        if j.get("state") not in ("queued", "running"):
            continue
        eta = job_eta_s(j)
        if eta is None:
            unknown += 1
        else:
            total += eta
            known += 1
    if not known:
        return None
    return {"seconds": total, "jobs": known, "unknown": unknown}
