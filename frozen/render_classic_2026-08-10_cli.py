"""
render_classic.py  —  ordinary Blender renders, with resume
===========================================================

Stills and animations: no tiles, no camera shift, no special maths.

THE .BLEND IS IN CHARGE.  Resolution, resolution %, samples, engine, output
path, file format, colour management and frame range all come from the scene
exactly as saved — this script overrides none of them, so what you get is what
F12 would give you.  (render_tiles_unlimited.py overrides resolution because it
must: it renders 4096 px tiles of a 120 000 px image.  Here there is no reason
to touch anything.)

The only settings this script will change are ones you explicitly ask it to in
the job config, and every one of them defaults to "leave the .blend alone":
frame range, output folder, camera, compute device.

Why a script at all, rather than the app calling `blender -f N` per frame:
loading the scene is the expensive part.  A LiDAR scene is a gigabyte of point
cloud, and reloading it for every frame would dominate the render.  This loads
once and loops inside, skipping frames already on disk — the same resume
behaviour as render_tiles_unlimited.py.

    set RENDERFARM_JOB=C:\\...\\queue\\job_0007.json
    blender --background scene.blend --python render_classic.py

Progress is written to <job file dir>\\<job id>_manifest.json after every frame,
so a stop or a power cut never loses more than the frame in flight.  The
manifest lives with the app, NOT in the render output folder — classic renders
write into folders the user already organises by hand, and the app does not
leave bookkeeping there.
"""

import bpy
import datetime
import json
import os
import time


# ── CONFIG ──────────────────────────────────────────────────────────────────

MODE          = "animation"  # "still" or "animation"
FRAME         = 0            # still mode: frame to render (0 = scene current)
FRAME_START   = 0            # animation: 0 = use the scene's own range
FRAME_END     = 0
FRAME_STEP    = 0            # 0 = use the scene's step

SCENE         = ""           # which scene to render;  "" = the .blend's active scene
OUTPUT_DIR    = ""           # "" = use the .blend's own output path
CAMERA        = ""           # "" = use the scene's active camera
SKIP_EXISTING = True         # resume: don't re-render frames already on disk

# File format. "" = use the .blend's own setting, which is still the default.
# One of PNG, JPEG, WEBP, OPEN_EXR, TIFF, BMP, TARGA, FFMPEG, ...
# Changing it changes the file extension, so it also changes which frames count
# as already rendered — only ever set this before a job has started.
FILE_FORMAT   = ""

# Compute device.  "" = leave the .blend's own setting alone (the default:
# these scenes are already configured for GPU).  "GPU" or "CPU" to override.
DEVICE         = ""
COMPUTE_DEVICE = "OPTIX"     # only consulted when DEVICE == "GPU"

# NOTE: there is deliberately no SAMPLES, RESOLUTION, FILE_FORMAT or
# OUTPUT_FILE setting here.  Those belong to the .blend.

# ── END CONFIG ──────────────────────────────────────────────────────────────


# ── RenderFarm job override ─────────────────────────────────────────────────
# Same contract as render_tiles_unlimited.py: RENDERFARM_JOB names a job JSON
# whose "config" object overrides the constants above.  Deliberately duplicated
# rather than shared — a frozen snapshot of this file must stay self-contained
# and runnable years from now.  (Only config plumbing is duplicated; none of
# the render maths that must never drift lives here.)

_JOB_OVERRIDABLE = {
    "SCENE",
    "MODE", "FRAME", "FRAME_START", "FRAME_END", "FRAME_STEP",
    "OUTPUT_DIR", "CAMERA", "SKIP_EXISTING", "DEVICE", "COMPUTE_DEVICE",
    "FILE_FORMAT",
}

JOB      = {}
JOB_PATH = ""


def _apply_job_override():
    global JOB, JOB_PATH
    job_path = os.environ.get("RENDERFARM_JOB", "").strip().strip('"')
    if not job_path:
        return
    if not os.path.isfile(job_path):
        raise FileNotFoundError(f"RENDERFARM_JOB points to a missing file: {job_path}")

    # utf-8-sig: PowerShell writes a BOM that plain json.load rejects.
    with open(job_path, encoding="utf-8-sig") as f:
        job = json.load(f)

    JOB, JOB_PATH = job, job_path
    cfg = job.get("config", {})
    if not isinstance(cfg, dict):
        raise TypeError(f'{job_path}: "config" must be an object')

    g, applied = globals(), []
    for key, value in cfg.items():
        if key not in _JOB_OVERRIDABLE:
            raise KeyError(f"{job_path}: unknown config key {key!r}.  Allowed: "
                           f"{', '.join(sorted(_JOB_OVERRIDABLE))}")
        current = g[key]
        if isinstance(current, bool):
            ok = isinstance(value, bool)
        elif isinstance(current, int):
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, str)
        if not ok:
            raise TypeError(f"{job_path}: {key} expects {type(current).__name__}, "
                            f"got {type(value).__name__} ({value!r})")
        g[key] = value
        applied.append((key, value))

    print(f"\n  Job file       :  {job_path}", flush=True)
    print(f"  Job            :  {job.get('id','?')} {job.get('name','')}".rstrip(), flush=True)
    for k, v in applied:
        print(f"      {k:<16}=  {v!r}", flush=True)
    print("", flush=True)


def _apply_scene():
    """
    Switch to the scene named by SCENE, if one was asked for.

    A .blend can hold several scenes, each with its own cameras, frame range,
    resolution and output path. Everything below reads bpy.context.scene, so
    switching it once here is the whole feature.

    THE .BLEND IS STILL IN CHARGE: this picks which scene's settings to obey,
    it does not override any of them.

    bpy.context.window exists under -b — Blender creates one window in the
    window manager even in background mode — so a plain assignment is enough.
    """
    if not SCENE:
        return
    target = bpy.data.scenes.get(SCENE)
    if target is None:
        raise RuntimeError(
            f"scene {SCENE!r} not found in this .blend. "
            f"Available: {', '.join(s.name for s in bpy.data.scenes)}")
    win = bpy.context.window
    if win is None:
        wins = bpy.context.window_manager.windows
        win = wins[0] if wins else None
    if win is None:
        raise RuntimeError("cannot switch scene: no window in the window manager")
    win.scene = target
    print(f"  scene        : {target.name}  (selected by the job)", flush=True)


try:
    _apply_job_override()
    _apply_scene()
except Exception:
    # Blender exits 0 after an exception at --python import time; the queue
    # would record a broken job as done.  Exit explicitly.
    import sys as _sys
    import traceback as _traceback
    _traceback.print_exc()
    _sys.stdout.flush()
    _sys.exit(1)


# ── helpers ─────────────────────────────────────────────────────────────────

def _hms(s):
    s = int(s or 0)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


def _now():
    return (datetime.datetime.now(datetime.timezone.utc)
            .astimezone().isoformat(timespec="seconds"))


def manifest_path():
    """Beside the job file, named for the job — never in the render folder."""
    if not JOB_PATH:
        return None
    return os.path.join(os.path.dirname(JOB_PATH),
                        f"{JOB.get('id','job')}_manifest.json")


# Movie containers produce ONE file for the whole range, so frames cannot be
# skipped or resumed individually — the range must be rendered in one pass.
MOVIE_FORMATS = {"FFMPEG", "AVI_JPEG", "AVI_RAW"}


def setup_device(scene):
    """Leave the .blend's device alone unless DEVICE explicitly says otherwise."""
    if not DEVICE:
        try:
            current = scene.cycles.device
        except AttributeError:
            current = r"(engine has no device setting)"
        print(f"  Render device : {current}  (from the .blend)", flush=True)
        return
    if DEVICE == "CPU":
        scene.cycles.device = "CPU"
        print("  Render device : CPU  (overridden by job)", flush=True)
        return
    try:
        cprefs = bpy.context.preferences.addons["cycles"].preferences
        cprefs.compute_device_type = COMPUTE_DEVICE
        cprefs.get_devices()
        for dev in cprefs.devices:
            dev.use = True
        scene.cycles.device = "GPU"
        print(f"  Render device : GPU [{COMPUTE_DEVICE}]  (overridden by job)",
              flush=True)
    except Exception as e:
        print(f"  ⚠  GPU override failed ({e}) — using the .blend's setting",
              flush=True)


def render_classic():
    scene = bpy.context.scene
    r     = scene.render

    state = {"filepath": r.filepath,
             "frame": scene.frame_current,
             "camera": scene.camera}

    if CAMERA:
        cam = bpy.data.objects.get(CAMERA)
        if cam is None:
            raise RuntimeError(f"camera {CAMERA!r} not found in the scene")
        scene.camera = cam
    if scene.camera is None:
        raise RuntimeError("No active camera in the scene.")

    # Passed through exactly as given. This is a Blender output path, not
    # simply a folder: a trailing separator means "write into this directory",
    # while anything else is a FILENAME PREFIX — ".../still/shot_" writes
    # shot_0004.png, which is deliberate and used. Normalising it to a folder
    # here breaks that, so the trailing separator belongs to whoever sets the
    # value, not to this script.
    if OUTPUT_DIR:
        r.filepath = OUTPUT_DIR

    # Before anything reads frame_path(): the format decides the extension, and
    # therefore which files count as already rendered.
    if FILE_FORMAT:
        try:
            r.image_settings.file_format = FILE_FORMAT
            print(f"  format       : {FILE_FORMAT}  (overridden by job)",
                  flush=True)
        except TypeError:
            raise RuntimeError(
                f"{FILE_FORMAT!r} is not a file format Blender knows")

    setup_device(scene)

    fmt      = r.image_settings.file_format
    is_movie = fmt in MOVIE_FORMATS
    template = r.filepath

    # ── Which frames ─────────────────────────────────────────────────────
    if MODE == "still":
        frames = [FRAME or scene.frame_current]
    else:
        start = FRAME_START or scene.frame_start
        end   = FRAME_END   or scene.frame_end
        step  = FRAME_STEP  or scene.frame_step or 1
        frames = list(range(start, end + 1, step))

    out_dir = os.path.dirname(bpy.path.abspath(r.frame_path(frame=frames[0])))

    print(f"\n{'═' * 64}", flush=True)
    print(f"  CLASSIC RENDER", flush=True)
    print(f"  scene        : {bpy.path.basename(bpy.data.filepath) or '(unsaved)'}", flush=True)
    print(f"  camera       : {scene.camera.name}", flush=True)
    print(f"  engine       : {r.engine}", flush=True)
    # Echo the scene's own settings so it is obvious nothing was overridden.
    print(f"  resolution   : {r.resolution_x} × {r.resolution_y}"
          f"  @ {r.resolution_percentage}%"
          f"  →  {round(r.resolution_x * r.resolution_percentage / 100)}"
          f" × {round(r.resolution_y * r.resolution_percentage / 100)} px", flush=True)
    try:
        _s = scene.cycles
        print(f"  samples      : {_s.samples}"
              f"{'  (adaptive, min ' + str(_s.adaptive_min_samples) + ')' if _s.use_adaptive_sampling else '  (fixed)'}",
              flush=True)
    except AttributeError:
        pass                      # not Cycles — nothing to report
    print(f"  format       : {fmt}{'  (movie — no per-frame resume)' if is_movie else ''}",
          flush=True)
    print(f"  mode         : {MODE}", flush=True)
    print(f"  frames       : {frames[0]}" +
          (f" → {frames[-1]}  ({len(frames)} frames)" if len(frames) > 1 else ""),
          flush=True)
    print(f"  output       : {out_dir}", flush=True)
    print(f"{'═' * 64}\n", flush=True)

    mpath = manifest_path()
    old   = {}
    if mpath and os.path.exists(mpath):
        try:
            with open(mpath, encoding="utf-8-sig") as f:
                old = json.load(f)
        except Exception:
            old = {}

    total = 1 if is_movie else len(frames)
    # A near-square grid so the UI's map stays readable for long animations
    # instead of becoming a 1 × 240 hairline.
    cols  = min(24, total) or 1
    rows  = (total + cols - 1) // cols

    manifest = {
        "kind":        "frames",
        "unit":        "frames",
        "mode":        MODE,
        "output_dir":  out_dir,
        "format":      fmt,
        "is_movie":    is_movie,
        "frames":      [frames[0], frames[-1]],
        "cols":        cols,
        "rows":        rows,
        "total":       total,
        "frames_done": 0,
        "frame_times": dict(old.get("frame_times", {})),
        "grid":        "0" * (cols * rows),
        "started_at":  old.get("started_at", _now()),
        "errors":      [],
    }

    # Carry the previous session's last image forward if it is still on disk,
    # so a resumed job shows the render immediately instead of a blank preview
    # until the first new frame lands.
    _prev = old.get("last_file")
    if _prev and os.path.isfile(_prev):
        manifest["last_file"] = _prev

    def write_manifest():
        if not mpath:
            return
        try:
            tmp = mpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            os.replace(tmp, mpath)
        except OSError:
            pass

    def mark(idx, done=True):
        g = list(manifest["grid"])
        if 0 <= idx < len(g):
            g[idx] = "1" if done else "0"
        manifest["grid"] = "".join(g)

    session_start = time.time()
    times         = []
    done_count    = 0

    # ── Let Blender render it, so the progress reaches the log ───────────
    #
    # bpy.ops.render.render() prints NOTHING in background mode. Blender's own
    # render loop — the one "-f 1" starts — prints a line per pass:
    #
    #   00:13.390 render | Fra:1 | Remaining: 00:12.34 | Mem: 12M | Sample 1025/2000
    #
    # Measured on one scene, same settings, same 26s render: 4 sample lines
    # from "-f", 0 from bpy.ops. runner.py has always known how to parse that
    # line; it simply never had one to read.
    #
    # So for a STILL the runner passes "-f <frame>" and sets this variable, and
    # everything below is skipped: Blender renders after this script returns,
    # and the bookkeeping happens in handlers instead of in the loop.
    #
    # Stills only. An animation's frame list depends on which frames are
    # already on disk, which is not known until the .blend is open, and moving
    # that decision out here would mean loading a 1.4 GB scene twice.
    if os.environ.get("RENDAFAR_CLI_RENDER") == "1" and not is_movie:
        if len(frames) == 1 and SKIP_EXISTING:
            r.filepath = template
            if os.path.exists(bpy.path.abspath(r.frame_path(frame=frames[0]))):
                print(f"  [    1/1] SKIP  frame {frames[0]} — already on disk",
                      flush=True)
                mark(0)
                manifest["frames_done"] = 1
                write_manifest()
                return manifest

        write_manifest()
        _cli_state = {"t0": None}

        def _on_pre(*_a):
            _cli_state["t0"] = time.time()

        def _on_write(*_a):
            # Fires once the file is on disk, which is what "done" means here.
            f = frames[0]
            target = bpy.path.abspath(r.frame_path(frame=f))
            elapsed = time.time() - (_cli_state["t0"] or time.time())
            mark(0)
            manifest["frames_done"] = 1
            manifest["frame_times"][str(f)] = round(elapsed, 2)
            manifest["last_file"] = target
            write_manifest()
            print(f"\n  [    1/1]  frame {f}  →  {os.path.basename(target)}"
                  f"   in {_hms(elapsed)}", flush=True)
            print(f"\n{'═' * 64}\n  1/1 frames  |  {len(manifest['errors'])} errors"
                  f"\n  output  →  {out_dir}\n{'═' * 64}", flush=True)

        bpy.app.handlers.render_pre.append(_on_pre)
        bpy.app.handlers.render_write.append(_on_write)
        print(f"  [    1/1]  frame {frames[0]}  — rendering, progress below",
              flush=True)
        return manifest

    try:
        # ── Movie: one pass, no resume ───────────────────────────────────
        if is_movie:
            print("  Rendering the whole range in one pass (movie container).\n",
                  flush=True)
            t0 = time.time()
            scene.frame_start, scene.frame_end = frames[0], frames[-1]
            bpy.ops.render.render(animation=True)
            manifest["frames_done"] = 1
            manifest["last_file"]   = bpy.path.abspath(r.frame_path(frame=frames[0]))
            mark(0)
            manifest["frame_times"]["movie"] = round(time.time() - t0, 2)
            write_manifest()
            print(f"\n  Done in {_hms(time.time() - t0)}", flush=True)
            return manifest

        # ── Frames ───────────────────────────────────────────────────────
        # Count what already exists first, so the resume line is honest and
        # the progress bar doesn't start at zero on a resumed job.
        existing = []
        for i, f in enumerate(frames):
            r.filepath = template
            if os.path.exists(bpy.path.abspath(r.frame_path(frame=f))):
                existing.append(i)
        if existing and SKIP_EXISTING:
            for i in existing:
                mark(i)
            done_count = len(existing)
            manifest["frames_done"] = done_count
            print(f"  Resume: {done_count}/{total} frames already on disk\n",
                  flush=True)
        write_manifest()

        for i, f in enumerate(frames):
            r.filepath = template
            target = bpy.path.abspath(r.frame_path(frame=f))

            if SKIP_EXISTING and os.path.exists(target):
                print(f"  [{i+1:>5}/{total}] SKIP  frame {f}", flush=True)
                continue

            print(f"  [{i+1:>5}/{total}]  frame {f}  →  "
                  f"{os.path.basename(target)}", flush=True)
            t0 = time.time()
            try:
                scene.frame_set(f)
                # Blender appends the extension itself, so hand it the path
                # without one — this reproduces the scene's own naming exactly.
                r.filepath = os.path.splitext(target)[0]
                bpy.ops.render.render(write_still=True)
            except Exception as exc:
                print(f"         !! ERROR: {exc}", flush=True)
                manifest["errors"].append(f)
                write_manifest()
                continue
            finally:
                r.filepath = template

            elapsed = time.time() - t0
            times.append(elapsed)
            done_count += 1
            mark(i)
            manifest["frames_done"] = done_count
            manifest["frame_times"][str(f)] = round(elapsed, 2)
            manifest["last_file"] = target
            write_manifest()

            avg       = sum(times) / len(times)
            remaining = total - done_count
            print(f"         ↳  frame {_hms(elapsed)}  |  avg {_hms(avg)}"
                  f"  |  elapsed {_hms(time.time() - session_start)}"
                  f"  |  ETA {_hms(avg * remaining)}", flush=True)

    finally:
        r.filepath    = state["filepath"]
        scene.camera  = state["camera"]
        try:
            scene.frame_set(state["frame"])
        except Exception:
            pass
        write_manifest()
        print("\n  Render settings restored.", flush=True)

    print(f"\n{'═' * 64}", flush=True)
    print(f"  {done_count}/{total} frames  |  {len(manifest['errors'])} errors", flush=True)
    if manifest["errors"]:
        print(f"  Failed frames: {manifest['errors']}", flush=True)
    print(f"  output  →  {out_dir}", flush=True)
    print(f"{'═' * 64}\n", flush=True)
    return manifest


if __name__ == "__main__":
    import sys as _sys
    import builtins as _builtins
    _orig = _builtins.print
    _builtins.print = lambda *a, **kw: _orig(*a, **{**kw, "flush": True})
    try:
        render_classic()
    except Exception:
        import traceback
        traceback.print_exc()
        _sys.stdout.flush()
        _sys.exit(1)
