"""
Where a job's render output lands.

This mirrors the output-path logic inside render_tiles_unlimited.py so the app
can find manifest.json without guessing.  It must stay in sync with the render
script:

    name    = RENDER_NAME or <blend basename without .blend> or "render"
    abs_out = bpy.path.abspath(OUTPUT_DIR) / name
    manifest_path = abs_out / "manifest.json"

The app never creates these folders and never writes into them — it only reads.
"""

import json
import os
import time


def blender_abspath(path, blend_path):
    """Resolve a Blender path, including the '//' = blend-file-directory prefix."""
    if path.startswith("//"):
        base = os.path.dirname(os.path.abspath(blend_path))
        return os.path.normpath(os.path.join(base, path[2:]))
    return os.path.abspath(path)


def output_dir_for(blend_path, output_dir="//tiles/", render_name=""):
    """Absolute folder that receives the tiles and manifest.json."""
    name = (render_name
            or os.path.basename(blend_path).replace(".blend", "")
            or "render")
    return os.path.join(blender_abspath(output_dir, blend_path), name)


def split_output(value, job_type):
    """
    Turn a folder into the settings the render script expects.

    The UI shows one thing — the folder a render writes to — but the two script
    types store it differently. A classic render writes straight to OUTPUT_DIR.
    A tiled render joins OUTPUT_DIR with RENDER_NAME, so the folder has to be
    split into its parent and its last component; putting the whole path in
    RENDER_NAME would nest it inside itself and the render would write
    somewhere nobody thinks to look.

    Lives here rather than in the UI so it can be tested without Qt, and so
    there is one answer rather than one per front end.
    """
    value = (value or "").strip().strip('"')
    if not value:
        # Empty means "back to the default": the .blend's own output path for a
        # classic render, //tiles/<blend name> for a tiled one.
        return {"OUTPUT_DIR": "", "RENDER_NAME": ""} if job_type == "tiled" \
            else {"OUTPUT_DIR": ""}
    if job_type != "tiled":
        return {"OUTPUT_DIR": value}

    value = os.path.normpath(value)
    parent, name = os.path.split(value.rstrip("\\/"))
    if not parent or not name:
        return {"OUTPUT_DIR": value}          # a drive root: keep it whole
    return {"OUTPUT_DIR": parent, "RENDER_NAME": name}


def classic_manifest_path(job):
    """
    Classic jobs keep their manifest beside the job file, not in the render
    folder — those folders are organised by hand and the app does not leave
    bookkeeping in them.  Must match manifest_path() in render_classic.py.
    """
    import config
    return os.path.join(config.QUEUE_DIR, f"{job['id']}_manifest.json")


def job_output_dir(job):
    """
    Where a job writes its output.

    Tiled jobs: computed from the config, known before the job ever runs.
    Classic jobs: comes from the .blend's own render path, so it is only known
    once Blender has resolved it — None until the first run.
    """
    if job.get("type") == "classic":
        m = read_json_tolerant(classic_manifest_path(job))
        return (m or {}).get("output_dir")
    cfg = job.get("config", {})
    return output_dir_for(
        job["blend"],
        cfg.get("OUTPUT_DIR", "//tiles/"),
        cfg.get("RENDER_NAME", ""),
    )


def read_json_tolerant(path, attempts=4, delay=0.15):
    """
    Read a JSON file that another process may be rewriting.

    render_tiles_unlimited.py rewrites manifest.json after every tile, so a read
    can land mid-write and see truncated JSON.  Retry briefly before giving up;
    returns None rather than raising, since a missing or momentarily unreadable
    manifest is a normal transient state, not an error.
    """
    for i in range(attempts):
        try:
            with open(path, encoding="utf-8-sig") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            if i == attempts - 1:
                return None
            time.sleep(delay)
    return None


def read_manifest(job):
    """Manifest dict for a job, or None if not readable yet."""
    if job.get("type") == "classic":
        return read_json_tolerant(classic_manifest_path(job))
    out = job_output_dir(job)
    if not out:
        return None
    return read_json_tolerant(os.path.join(out, "manifest.json"))


def progress(job):
    """
    Progress for any job: {done, total, percent, unit, ...} or None.

    Always read from the manifest, never parsed from stdout — it is flushed
    after every tile/frame and survives across sessions, so it stays correct
    through stops, crashes and power cuts.

    Both job types report the same shape so the UI needs no special cases; only
    the labels differ (tiles vs frames).
    """
    m = read_manifest(job)
    if not m:
        return None

    if m.get("kind") == "frames":
        total, done = m.get("total") or 0, m.get("frames_done") or 0
        times = [t for t in (m.get("frame_times") or {}).values() if t and t > 0]
        unit  = "frames"
    else:
        total = (m.get("cols") or 0) * (m.get("rows") or 0)
        done  = m.get("tiles_done") or 0
        times = [t for t in (m.get("tile_times") or {}).values() if t and t > 0]
        unit  = "tiles"

    avg = sum(times) / len(times) if times else None
    return {
        "done":       done,
        "total":      total,
        "unit":       unit,
        "percent":    round(100.0 * done / total, 1) if total else 0.0,
        "cols":       m.get("cols") or 0,
        "rows":       m.get("rows") or 0,
        "avg_tile_s": round(avg, 1) if avg else None,
        "eta_s":      round(avg * (total - done)) if avg and total else None,
        "errors":     m.get("errors") or [],
        "started_at": m.get("started_at"),
        "total_W":    m.get("total_W"),
        "total_H":    m.get("total_H"),
    }


def latest_tile(job):
    """
    (path, mtime) of the most recent image the render produced, or None.

    Tiled jobs: newest tile_*.ext in the output folder, ignoring _preview_*
    subfolders so a preview never masks the real render.
    Classic jobs: the frame the script last wrote, recorded in its manifest —
    no directory scan, and it can't pick up unrelated images that happen to
    share the output folder.
    """
    if job.get("type") == "classic":
        m = read_manifest(job) or {}
        p = m.get("last_file")
        if p and os.path.isfile(p):
            try:
                return (p, os.path.getmtime(p))
            except OSError:
                return None
        return None

    out = job_output_dir(job)
    if not out or not os.path.isdir(out):
        return None
    newest = None
    try:
        for entry in os.scandir(out):
            if not entry.is_file():
                continue
            n = entry.name
            if not n.startswith("tile_"):
                continue
            if not n.lower().endswith((".webp", ".jpg", ".jpeg", ".png")):
                continue
            mt = entry.stat().st_mtime
            if newest is None or mt > newest[1]:
                newest = (entry.path, mt)
    except OSError:
        return None
    return newest
