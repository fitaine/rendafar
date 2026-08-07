"""
Reading a .blend's settings.

Only Blender can read a .blend, so this runs it headless with a tiny script
that prints the scene's settings as JSON. That costs a few seconds, so results
are cached by (path, modification time): the same file is only ever probed
once, and editing the .blend invalidates the cache by itself.

Nothing here changes a render. It exists so the app can *show* what the .blend
says — camera, resolution, samples, frame range, output path — instead of
leaving them invisible until the render starts.
"""

import hashlib
import json
import os
import subprocess

import config

CACHE_DIR = os.path.join(config.APP_DIR, "cache")

# Bump when the probe learns a new field, so old entries are re-read rather
# than used with something missing.
CACHE_VERSION = 3
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Printed either side of the JSON so it can be found in Blender's very chatty
# startup output.
MARK = "___RENDAFAR_BLEND_INFO___"

PROBE = r"""
import bpy, json, os


def _scene_info(s):
    # Everything the app shows, for ONE scene.
    # (A comment, not a docstring: this whole block lives inside a triple
    # quoted string in blend_info.py, and a docstring would end it early.)
    r = s.render
    d = {
        "scene": s.name,
        "camera": s.camera.name if s.camera else None,
        # Cameras IN THIS SCENE, not every camera in the file: a camera that
        # lives in another scene cannot be rendered from this one, so offering
        # it would be a dropdown entry that can only fail.
        "cameras": [o.name for o in s.objects if o.type == 'CAMERA'],
        "engine": r.engine,
        "resolution_x": r.resolution_x,
        "resolution_y": r.resolution_y,
        "resolution_percentage": r.resolution_percentage,
        "pixel_aspect_x": r.pixel_aspect_x,
        "pixel_aspect_y": r.pixel_aspect_y,
        "final_x": round(r.resolution_x * r.resolution_percentage / 100),
        "final_y": round(r.resolution_y * r.resolution_percentage / 100),
        "fps": r.fps,
        "frame_start": s.frame_start,
        "frame_end": s.frame_end,
        "frame_step": s.frame_step,
        "frame_current": s.frame_current,
        "file_format": r.image_settings.file_format,
        "color_depth": getattr(r.image_settings, "color_depth", None),
        "filepath": bpy.path.abspath(r.filepath),
        "film_transparent": getattr(r, "film_transparent", None),
    }
    try:
        c = s.cycles
        d["samples"] = c.samples
        d["adaptive"] = bool(c.use_adaptive_sampling)
        d["adaptive_min_samples"] = c.adaptive_min_samples
        d["adaptive_threshold"] = round(c.adaptive_threshold, 5)
        d["device"] = c.device
        d["denoise"] = bool(getattr(c, "use_denoising", False))
    except AttributeError:
        pass                      # not Cycles
    return d


active = bpy.context.scene
info = dict(_scene_info(active))          # the active scene stays at the top
info["_v"] = 3
info["active_scene"] = active.name
info["scenes"] = [_scene_info(s) for s in bpy.data.scenes]
try:
    info["objects"] = len(bpy.data.objects)
except Exception:
    pass
print("%s" + json.dumps(info) + "%s")
""" % (MARK, MARK)


def _cache_path(blend):
    try:
        mtime = os.path.getmtime(blend)
    except OSError:
        mtime = 0
    key = hashlib.sha1(f"{os.path.abspath(blend)}|{mtime}".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"blend_{key}.json")


def read(blend, refresh=False):
    """
    Settings of a .blend, as a dict. `{"error": ...}` if it cannot be read.

    Cached by path and modification time, so the cost is paid once per file
    and a re-saved .blend is picked up automatically.
    """
    blend = os.path.abspath(blend)
    if not os.path.isfile(blend):
        return {"error": "file not found"}

    cache = _cache_path(blend)
    if not refresh and os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                cached = json.load(f)
            # Version check, not just presence: an entry written before a new
            # field existed would otherwise be used with that field missing,
            # and a silently-defaulted pixel aspect would give a wrong grid.
            if cached.get("_v") == CACHE_VERSION:
                return cached
        except (OSError, ValueError):
            pass                          # unreadable cache: probe again

    try:
        out = subprocess.run(
            [config.BLENDER_EXE, "-b", blend, "--python-expr", PROBE],
            capture_output=True, text=True, timeout=180,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Blender took too long to open the file"}
    except OSError as e:
        return {"error": f"could not run Blender: {e}"}

    text = out.stdout or ""
    if MARK not in text:
        tail = (out.stderr or text).strip().splitlines()[-3:]
        return {"error": "Blender did not report the settings",
                "detail": "\n".join(tail)}
    try:
        info = json.loads(text.split(MARK)[1])
    except (IndexError, ValueError) as e:
        return {"error": f"unreadable reply ({e})"}

    info["blend"] = blend
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        tmp = cache + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        os.replace(tmp, cache)
    except OSError:
        pass                              # caching is an optimisation, not a need
    return info
