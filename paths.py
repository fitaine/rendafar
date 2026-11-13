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

import datetime
import json
import os
import re
import time
import urllib.parse


def clean_path(raw):
    r"""Normalise a path typed or pasted by a person.

    A path reaches the app in several mangled forms and every one of them
    fails `os.path.isfile` with an unhelpful "file not found":

        "C:\....blend"                 Shift+right-click, Copy as path
        C:\...\Piste%20de%20bobsleigh    copied from anywhere URL-encoding
        file:///C:/.../a.blend            dragged from a browser
        C:/.../a.blend                    forward slashes

    Percent-decoding is only applied when a '%' is present, so a filename that
    legitimately contains one is left alone.
    """
    if raw is None:
        return ""
    p = str(raw).strip().strip('"').strip("'")
    if p.lower().startswith("file:///"):
        p = p[8:]
    elif p.lower().startswith("file://"):
        p = p[7:]
    if "%" in p:
        decoded = urllib.parse.unquote(p)
        # Only take the decoded form if it actually helps, so a real '%' in a
        # name is not mangled into something that does not exist.
        if os.path.exists(decoded) or not os.path.exists(p):
            p = decoded
    if len(p) > 2 and p[1] == ":":
        p = p.replace("/", os.sep)
    return p


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


_overridable_cache = {}


def script_overridable(script_path):
    """
    The config keys a render script will accept, read from the script itself.

    Jobs pin a dated snapshot, so an older job's script may not know a setting
    that exists today — FILE_FORMAT arrived in the classic script on
    2026-07-27. Asking the script rather than assuming from the job type means
    the refusal comes at edit time, with a reason, instead of the render
    failing hours later.

    Read with ast: the scripts import bpy at module level and cannot be
    imported outside Blender.
    """
    import ast

    key = os.path.abspath(script_path)
    try:
        mtime = os.path.getmtime(key)
    except OSError:
        return None                       # unknown: let the caller not block
    hit = _overridable_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]

    try:
        tree = ast.parse(open(key, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return None
    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_JOB_OVERRIDABLE"
                for t in node.targets):
            try:
                found = set(ast.literal_eval(node.value))
            except (ValueError, TypeError):
                found = None
    _overridable_cache[key] = (mtime, found)
    return found


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


def output_problem(output, source="The output"):
    """
    Why Blender could not save to this output path, or None if it can.

    A path with no drive — "Chamechaude.tif_Camera_", or Blender's own default
    "/tmp\\" — is resolved against whatever folder Blender was started from,
    which for the app is C:\\. Windows refuses writes there, and Blender only
    finds out at the very end: a 10 minute render was thrown away exactly
    like this, with "output : C:\\" printed at the start for anyone watching.
    "//" is fine; it means "beside the .blend".
    """
    p = (output or "").strip().strip('"')
    if p.startswith("//"):
        return None
    drive, rest = os.path.splitdrive(p)
    if drive and rest.startswith(("\\", "/")):
        return None
    return (f"{source} is {p!r}, which has no folder, so Blender would try to "
            f"save to the root of C:\\ and fail after rendering. Give it a full "
            f"folder (C:\\...\\renders\\name_), or start it with // to save "
            f"beside the .blend.")


def unique_classic_output(base, cfg, name, taken=(), typed=False):
    """
    An output path for a classic job that no other job can write over.

    Blender names a still from the frame number alone, so several jobs pointed
    at one folder all produce 0001.png and the last one wins — silently, with
    no error and nothing to notice until the folder is opened. A warning is no
    use for a queue that runs unattended, so the paths are made distinct
    instead of the clash being reported.

    Two rules, both about how Blender reads a filepath:

      * a path with no trailing separator is a FILENAME PREFIX, not a folder,
        so "…\\renders" writes renders0001.png BESIDE the folder. If it names a
        real directory, the separator is added.
      * whatever the folder, the camera (or scene, or job name) is folded into
        the filename, giving Camera_sud_0001.png rather than 0001.png.

    `taken` is the set of paths other jobs already resolve to; a counter is
    appended if the natural name is somehow still a duplicate.

    The result is stored on the job, so it stays put across resumes — which
    SKIP_EXISTING depends on, since it decides what to re-render by looking for
    files at exactly this path.

    `typed` is a filename someone just wrote in the Filename cell. It is kept
    as written, only numbered if another job already has it: folding the camera
    in turned "print_" into "print_Camera_main_", and retyping the name the
    cell showed doubled the camera every time.
    """
    base = (base or "").strip().strip('"')
    stem = cfg.get("CAMERA") or cfg.get("SCENE") or name or "render"
    safe = re.sub(r"[^\w.-]+", "_", str(stem)).strip("_") or "render"

    if not base:
        return ""                    # nothing to anchor to: the .blend decides
    if typed and not base.endswith(("/", "\\")):
        out, n = base, 2
        while out in set(taken):
            out = f"{base.rstrip('_-')}_{n}_"
            n += 1
        return out
    if base.endswith(("/", "\\")):
        head = base
    elif os.path.isdir(base):
        head = base + os.sep         # a real folder, just missing its slash
    else:
        head = base.rstrip("_-") + "_"   # an intentional prefix: extend it

    out = f"{head}{safe}_"
    n = 2
    while out in set(taken):
        out = f"{head}{safe}_{n}_"
        n += 1
    return out


EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "OPEN_EXR": "exr",
       "TIFF": "tif", "BMP": "bmp", "TARGA": "tga", "FFMPEG": "mp4"}


def split_classic_output(value):
    """
    A classic OUTPUT_DIR as the two things it really is: (folder, filename).

    Blender stores one string and reads it as a folder when it ends in a
    separator and as a FILENAME PREFIX otherwise, which is why
    "…\\Ferme-des-Crozets\\renders" writes renders0001.png beside the folder
    rather than inside it. Presenting it as one field hides that entirely; a
    typo of "render" for "renders" is then invisible until the render is over.
    """
    value = (value or "").strip().strip('"')
    if not value:
        return "", ""
    if value.endswith(("/", "\\")):
        return value, ""
    if os.path.isdir(value):
        return value + os.sep, ""
    head, tail = os.path.split(value)
    return (head + os.sep if head else ""), tail


def join_classic_output(folder, filename):
    """The single string the render script wants, from the two shown."""
    folder = (folder or "").strip().strip('"')
    filename = (filename or "").strip().strip('"')
    if folder and not folder.endswith(("/", "\\")):
        folder += os.sep
    return folder + filename


def preview_filename(filename, file_format="PNG", frame=1):
    """
    What one rendered file will be called — the thing worth checking BEFORE a
    render, not after.

    Blender appends a four digit frame number and the format's extension, so a
    filename of "" gives 0001.png: distinct per frame, identical between two
    jobs pointed at one folder.
    """
    ext = EXT.get((file_format or "PNG").upper(), (file_format or "png").lower())
    try:
        n = int(frame)
    except (TypeError, ValueError):
        n = 1
    return f"{filename or ''}{n:04d}.{ext}"


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
    eta_s = round(avg * (total - done)) if avg and total else None
    # A duration is the wrong unit for a render measured in days: "ETA 63h 20m"
    # takes arithmetic to act on, while a wall-clock date answers the question
    # actually being asked ("can I have the machine back on Thursday?").
    # Computed here, next to eta_s, so the desktop app, the phone, the CLI and
    # the Telegram updates all quote the same instant.
    eta_at = None
    if eta_s:
        eta_at = (datetime.datetime.now()
                  + datetime.timedelta(seconds=eta_s)).isoformat(timespec="seconds")
    return {
        "done":       done,
        "total":      total,
        "unit":       unit,
        "percent":    round(100.0 * done / total, 1) if total else 0.0,
        "cols":       m.get("cols") or 0,
        "rows":       m.get("rows") or 0,
        "avg_tile_s": round(avg, 1) if avg else None,
        "eta_s":      eta_s,
        "eta_at":     eta_at,
        "errors":     m.get("errors") or [],
        "started_at": m.get("started_at"),
        "total_W":    m.get("total_W"),
        "total_H":    m.get("total_H"),
    }


def format_eta_clock(eta_at, now=None):
    """
    An `eta_at` ISO stamp as something readable at a glance.

    The day matters more than the date here: these renders run 40-80 hours, so
    a finish time is routinely two or three sleeps away and "Thu 04:15" is what
    the question was really about. The date is only spelled out once the day
    name stops being unambiguous, a week out.

    Returns "" for anything missing or unparseable, so callers can concatenate
    it without guarding.
    """
    if not eta_at:
        return ""
    try:
        end = datetime.datetime.fromisoformat(eta_at)
    except (TypeError, ValueError):
        return ""
    now = now or datetime.datetime.now()

    clock = end.strftime("%H:%M")
    days = (end.date() - now.date()).days
    if days <= 0:
        return f"today {clock}"
    if days == 1:
        return f"tomorrow {clock}"
    if days < 7:
        return f"{end.strftime('%a')} {clock}"
    return f"{end.strftime('%a %d %b')} {clock}"


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
