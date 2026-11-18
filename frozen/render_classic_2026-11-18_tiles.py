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

# Tiled still: 0 = off (one render, as F12).  >0 = cut the still into tiles of
# about this many pixels a side, render each as a border crop of the REAL
# camera (no zoom, no shift: the framing cannot drift), save each tile as soon
# as it is done, then stitch and run the scene's own compositor ONCE on the
# whole image. A power cut costs one tile, not the night. See render_tiled_still.
TILE_SIZE     = 0

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
    "FILE_FORMAT", "TILE_SIZE",
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


# ── tiled still ─────────────────────────────────────────────────────────────
#
# Why border crops and not the camera-shift tiling of render_tiles_unlimited:
# a border crop renders exactly the pixels F12 would, through the scene's own
# camera, untouched. Cycles seeds its noise from the pixel's position in the
# FULL frame, so even the noise of a tile matches the one-go render; there is
# nothing at a tile edge for a seam to come from.
#
# What does seam is the compositor (Denoise, Glare read neighbouring pixels),
# so tiles are rendered RAW into multilayer EXR, with every pass the scene
# writes, and the scene's own compositor runs once on the stitched image.

def _tile_bounds(W, H, size):
    cols = max(1, -(-W // size))
    rows = max(1, -(-H // size))
    xs = [round(k * W / cols) for k in range(cols + 1)]
    ys = [round(k * H / rows) for k in range(rows + 1)]
    return cols, rows, xs, ys


def _tile_name(row, col):
    return f"tile_r{row:02d}_c{col:02d}.exr"


def _update_preview(tile_dir, tiles, W, H, view_layer):
    """Quarter-size JPEG of the tiles done so far. Approximate colour (plain
    sRGB, not the scene's view transform): it is for checking, not judging."""
    import OpenImageIO as oiio
    from OpenImageIO import ImageBufAlgo as iba
    k = 4
    pw, ph = max(1, W // k), max(1, H // k)
    prev = oiio.ImageBuf(oiio.ImageSpec(pw, ph, 3, oiio.FLOAT))
    for (x0, y0, x1, y1), path in tiles:
        if not os.path.isfile(path):
            continue
        tb = oiio.ImageBuf(path)
        names = list(tb.spec().channelnames)
        want = [f"{view_layer}.Combined.{c}" for c in "RGB"]
        if not all(n in names for n in want):
            want = [n for n in names if n.endswith(("Combined.R", "Combined.G", "Combined.B"))][:3]
        rgb = iba.channels(tb, tuple(names.index(n) for n in want))
        tw, th = max(1, (x1 - x0) // k), max(1, (y1 - y0) // k)
        small = iba.resize(rgb, roi=oiio.ROI(0, tw, 0, th, 0, 1, 0, 3))
        iba.paste(prev, x0 // k, y0 // k, 0, 0, small)
    prev = iba.colorconvert(prev, "linear", "sRGB")
    out = os.path.join(tile_dir, "preview.jpg")
    tmp = os.path.join(tile_dir, "preview_tmp.jpg")
    prev.write(tmp, "uint8")
    os.replace(tmp, out)
    return out


def _read_all_parts(path):
    """One ImageBuf with the channels of EVERY part of a multipart EXR.

    Blender 5 writes a multilayer EXR as one part per pass (Combined, Normal,
    Denoising Albedo...). Reading only part 0 kept Combined and silently
    dropped the denoise data (found 2026-11-18)."""
    import OpenImageIO as oiio
    from OpenImageIO import ImageBufAlgo as iba
    inp = oiio.ImageInput.open(path)
    if not inp:
        raise RuntimeError(f"cannot open {path}: {oiio.geterror()}")
    names, n = [], 0
    while inp.seek_subimage(n, 0):
        names += list(inp.spec().channelnames)
        n += 1
    inp.close()
    buf = None
    for i in range(n):
        part = oiio.ImageBuf(path, i, 0)
        if not part.read(subimage=i, miplevel=0, force=True, convert=oiio.FLOAT):
            raise RuntimeError(f"cannot read part {i} of {path}: {part.geterror()}")
        buf = part if buf is None else iba.channel_append(buf, part)
    if list(buf.spec().channelnames) != names:      # keep the real pass names
        buf = iba.channels(buf, tuple(range(len(names))), newchannelnames=tuple(names))
    return buf


def _stitch(tiles, W, H, out_path):
    """Paste the raw tiles into one EXR, every pass of every part kept."""
    import OpenImageIO as oiio
    from OpenImageIO import ImageBufAlgo as iba
    first = _read_all_parts(tiles[0][1])
    spec = oiio.ImageSpec(first.spec())
    names = list(spec.channelnames)
    spec.width, spec.height = W, H
    spec.full_width, spec.full_height = W, H
    spec.x = spec.y = spec.full_x = spec.full_y = 0
    spec.tile_width = spec.tile_height = 0
    spec.set_format(oiio.FLOAT)
    spec.attribute("compression", "zip")
    full = oiio.ImageBuf(spec)
    for (x0, y0, x1, y1), path in tiles:
        tb = _read_all_parts(path)
        ts = tb.spec()
        if list(ts.channelnames) != names:
            raise RuntimeError(f"{os.path.basename(path)} has different passes "
                               f"from the first tile; delete it and resume")
        if (ts.width, ts.height) != (x1 - x0, y1 - y0):
            raise RuntimeError(f"{os.path.basename(path)} is {ts.width}x{ts.height}, "
                               f"expected {x1 - x0}x{y1 - y0}")
        if not iba.paste(full, x0, y0, 0, 0, tb):
            raise RuntimeError(f"paste failed: {oiio.geterror()}")
    tmp = out_path + ".tmp.exr"
    if not full.write(tmp):
        raise RuntimeError(f"could not write the stitched image: {full.geterror()}")
    os.replace(tmp, out_path)
    print(f"  passes kept: {sorted({n.rsplit('.', 1)[0] for n in names})}", flush=True)
    _write_photoshop(full, names, os.path.join(os.path.dirname(out_path),
                                               "stitched_photoshop.exr"))


def _write_photoshop(full, names, out_path):
    """Plain RGBA float EXR of Combined: Photoshop reads only R/G/B/A and
    shows the multilayer stitched.exr as black."""
    import OpenImageIO as oiio
    from OpenImageIO import ImageBufAlgo as iba
    idx = [next(i for i, n in enumerate(names) if n.endswith(f"Combined.{c}"))
           for c in "RGBA"]
    rgba = iba.channels(full, tuple(idx), newchannelnames=("R", "G", "B", "A"))
    rgba.specmod().attribute("compression", "zip")
    tmp = out_path + ".tmp.exr"
    if not rgba.write(tmp, oiio.FLOAT):
        print(f"  (Photoshop EXR not written: {rgba.geterror()})", flush=True)
        return
    os.replace(tmp, out_path)
    print(f"  Photoshop copy: {out_path}", flush=True)


def _composite(scene, stitched, final_path):
    """
    Run the scene's own compositor once, on the stitched image, and save the
    result exactly as F12 would: same view transform, same file format.

    Done in a throwaway scene holding a COPY of the compositor, whose Render
    Layers node is swapped for an Image node reading the stitched EXR. The real
    scene and its node tree are never touched.
    """
    r = scene.render
    W = int(r.resolution_x * r.resolution_percentage / 100)
    H = int(r.resolution_y * r.resolution_percentage / 100)
    vl_name = bpy.context.view_layer.name

    img = bpy.data.images.load(stitched, check_existing=False)
    tmp_scene = bpy.data.scenes.new("_rendafar_composite")
    group = None
    try:
        t = tmp_scene.render
        t.engine = "BLENDER_WORKBENCH"
        t.resolution_x, t.resolution_y, t.resolution_percentage = W, H, 100
        t.pixel_aspect_x, t.pixel_aspect_y = r.pixel_aspect_x, r.pixel_aspect_y
        t.dither_intensity = r.dither_intensity
        t.film_transparent = r.film_transparent

        vs, tv = scene.view_settings, tmp_scene.view_settings
        tmp_scene.display_settings.display_device = scene.display_settings.display_device
        tv.view_transform = vs.view_transform
        tv.look = vs.look
        tv.exposure = vs.exposure
        tv.gamma = vs.gamma
        if vs.use_curve_mapping:
            print("  ⚠  the scene uses view Curves; they are NOT copied to the "
                  "composite pass", flush=True)

        src, dst = r.image_settings, t.image_settings
        if hasattr(src, "media_type"):
            dst.media_type = src.media_type
        dst.file_format = src.file_format
        for attr in ("color_mode", "color_depth", "compression", "quality",
                     "exr_codec", "color_management"):
            try:
                setattr(dst, attr, getattr(src, attr))
            except (AttributeError, TypeError):
                pass

        tree = scene.compositing_node_group if r.use_compositing else None
        if tree is not None:
            group = tree.copy()
        else:
            group = bpy.data.node_groups.new("_rendafar_composite", "CompositorNodeTree")
            group.interface.new_socket("Image", in_out="OUTPUT",
                                       socket_type="NodeSocketColor")
            group.nodes.new("NodeGroupOutput")
            rl = group.nodes.new("CompositorNodeRLayers")
            out = next(n for n in group.nodes if n.bl_idname == "NodeGroupOutput")
            group.links.new(rl.outputs["Image"], out.inputs[0])

        for rl in [n for n in group.nodes if n.bl_idname == "CompositorNodeRLayers"]:
            node = group.nodes.new("CompositorNodeImage")
            node.image = img
            node.layer = rl.layer or vl_name
            node.location = rl.location
            for l in list(group.links):
                if l.from_node != rl:
                    continue
                name = l.from_socket.name
                # The Render Layers "Image" is the Combined pass in a file.
                src_sock = node.outputs.get("Combined" if name == "Image" else name)
                if src_sock is None or not src_sock.enabled:
                    # A pass the compositor is wired to but Cycles never
                    # produced (e.g. Denoising Albedo/Normal while render
                    # denoising is off). Render Layers hands over nothing for
                    # it either, so the input is left unconnected, as F12 has it.
                    print(f"  (compositor input {name!r}: not rendered, left empty)",
                          flush=True)
                    group.links.remove(l)
                    continue
                group.links.new(src_sock, l.to_socket)
            group.nodes.remove(rl)

        tmp_scene.compositing_node_group = group
        t.use_compositing = True

        bpy.ops.render.render(scene=tmp_scene.name)
        res = bpy.data.images["Render Result"]
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        part = os.path.splitext(final_path)[0] + "_partial" + os.path.splitext(final_path)[1]
        res.save_render(part, scene=tmp_scene)
        os.replace(part, final_path)
    finally:
        bpy.data.scenes.remove(tmp_scene)
        if group is not None:
            bpy.data.node_groups.remove(group)
        bpy.data.images.remove(img)


def render_tiled_still(scene, frame, template, out_dir, manifest, old, write_manifest):
    r = scene.render
    scene.frame_set(frame)
    r.filepath = template
    final = bpy.path.abspath(r.frame_path(frame=frame))

    W = int(r.resolution_x * r.resolution_percentage / 100)
    H = int(r.resolution_y * r.resolution_percentage / 100)
    cols, rows, xs, ys = _tile_bounds(W, H, TILE_SIZE)
    stem = os.path.splitext(os.path.basename(final))[0]
    tile_dir = os.path.join(os.path.dirname(final), f"_tiles_{stem}")
    os.makedirs(tile_dir, exist_ok=True)
    vl_name = bpy.context.view_layer.name

    tiles = []                       # ((x0, y0, x1, y1) from the TOP-left, path)
    for row in range(rows):
        for col in range(cols):
            tiles.append(((xs[col], ys[row], xs[col + 1], ys[row + 1]),
                          os.path.join(tile_dir, _tile_name(row, col))))
    total = len(tiles)

    manifest.update({
        "unit": "tiles", "cols": cols, "rows": rows, "total": total,
        "grid": "0" * total, "tile_dir": tile_dir, "final": final,
        "tile_size": TILE_SIZE,
    })
    manifest["frame_times"] = dict(old.get("frame_times", {}))

    # Settings that decide what a tile looks like. Tiles from a different
    # setup must never be stitched together, so a change refuses to resume.
    try:
        _c = scene.cycles
        _samp = [_c.samples, _c.use_adaptive_sampling, round(_c.adaptive_threshold, 6),
                 _c.adaptive_min_samples, _c.seed]
    except AttributeError:
        _samp = None
    layout = {"W": W, "H": H, "cols": cols, "rows": rows, "frame": frame,
              "camera": scene.camera.name, "sampling": _samp}
    lpath = os.path.join(tile_dir, "layout.json")
    if os.path.isfile(lpath):
        with open(lpath, encoding="utf-8") as f:
            prev = json.load(f)
        if prev != layout and (any(os.path.isfile(p) for _, p in tiles)
                               or os.path.isfile(os.path.join(tile_dir, "stitched.exr"))):
            raise RuntimeError(
                "the tiles on disk were rendered with different settings:\n"
                f"   on disk : {prev}\n   now     : {layout}\n"
                f"   delete {tile_dir} to start over")
    with open(lpath, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)

    def mark_existing():
        n = 0
        g = []
        for _, p in tiles:
            ok = os.path.isfile(p)
            g.append("1" if ok else "0")
            n += ok
        manifest["grid"] = "".join(g)
        return n

    print(f"  TILED STILL  : {cols} × {rows} = {total} tiles of ~{TILE_SIZE} px"
          f"  →  {os.path.basename(final)}", flush=True)
    print(f"  tiles folder : {tile_dir}", flush=True)

    if SKIP_EXISTING and os.path.exists(final):
        print(f"  final image already on disk, nothing to do", flush=True)
        manifest["grid"] = "1" * total
        manifest["frames_done"] = total
        manifest["last_file"] = final
        write_manifest()
        return manifest

    # Tiles already assembled and cleaned up, final image missing: the user
    # deleted it to redo the compositing (a Glare change, say). Recomposite
    # from the kept stitched image; nothing is rendered again.
    stitched = os.path.join(tile_dir, "stitched.exr")
    if (SKIP_EXISTING and os.path.isfile(stitched)
            and not any(os.path.isfile(p) for _, p in tiles)):
        print(f"  Tiles already assembled: compositing again from stitched.exr",
              flush=True)
        t0 = time.time()
        _composite(scene, stitched, final)
        print(f"  composite  {_hms(time.time() - t0)}", flush=True)
        manifest["grid"] = "1" * total
        manifest["frames_done"] = total
        manifest["last_file"] = final
        write_manifest()
        return manifest

    done = mark_existing() if SKIP_EXISTING else 0
    # The last tile only counts as done once the final image exists, so the
    # queue never calls the job finished between the last tile and the stitch.
    manifest["frames_done"] = min(done, total - 1)
    if done:
        print(f"  Resume: {done}/{total} tiles already on disk", flush=True)
        try:
            manifest["last_file"] = _update_preview(tile_dir, tiles, W, H, vl_name)
        except Exception as e:
            print(f"  (preview not updated: {e})", flush=True)
    write_manifest()

    s = r.image_settings
    keep = {"media": getattr(s, "media_type", None), "fmt": s.file_format, "depth": s.color_depth, "codec": s.exr_codec,
            "mode": s.color_mode, "border": r.use_border, "crop": r.use_crop_to_border,
            "comp": r.use_compositing, "ext": r.use_file_extension,
            "persist": r.use_persistent_data,
            "bx": (r.border_min_x, r.border_max_x, r.border_min_y, r.border_max_y)}
    times = []
    t_session = time.time()
    try:
        # Blender 5 moved multilayer out of file_format into media_type.
        if hasattr(s, "media_type"):
            s.media_type = "MULTI_LAYER_IMAGE"
        if s.file_format != "OPEN_EXR_MULTILAYER":
            s.file_format = "OPEN_EXR_MULTILAYER"
        s.color_depth = "32"     # full float, as asked: no precision lost before compositing
        s.exr_codec = "ZIP"
        s.color_mode = "RGBA"
        r.use_border = True
        r.use_crop_to_border = True
        r.use_compositing = False
        r.use_file_extension = True
        r.use_persistent_data = True      # the scene is synced once, not per tile
        # Denoising Albedo/Normal, so the stitched image can be denoised later.
        # Written even with render Denoise off (checked 2026-11-18, Blender 5.0).
        _vl = scene.view_layers[vl_name]
        keep["dpass"] = _vl.cycles.denoising_store_passes
        _vl.cycles.denoising_store_passes = True

        # bpy.ops.render.render() prints no progress in background mode, but
        # the render_stats handler still receives Cycles' status line
        # ("Remaining … | Mem … | Sample 992/2048"). Echo it, throttled, in the
        # format runner.py parses: live progress in the app, and the GPU
        # watchdog can tell a slow tile (samples still moving) from a hung one.
        # Tête du faisan tile 2 needed 9x tile 1 on 2026-11-12, at the same
        # ~65 W a hang shows, so power alone could not tell them apart.
        _last = {"t": 0.0, "s": None}

        def _stats(stats, *_):
            now = time.time()
            if now - _last["t"] < 15:
                return
            _last["t"] = now
            print(f"Fra:{frame} | {stats}", flush=True)

        bpy.app.handlers.render_stats.append(_stats)

        for i, ((x0, y0, x1, y1), path) in enumerate(tiles):
            if SKIP_EXISTING and os.path.isfile(path):
                continue
            # Blender truncates border*size to whole pixels; a quarter pixel
            # inside each edge lands on the intended pixel either way.
            r.border_min_x = (x0 + 0.25) / W
            r.border_max_x = (x1 + 0.25) / W
            r.border_min_y = (H - y1 + 0.25) / H
            r.border_max_y = (H - y0 + 0.25) / H
            part = path[:-4] + "_partial"
            print(f"  [{i+1:>3}/{total}]  tile r{i // cols} c{i % cols}"
                  f"  x {x0}-{x1}  y {y0}-{y1}", flush=True)
            t0 = time.time()
            r.filepath = part
            bpy.ops.render.render(write_still=True)
            written = part + ".exr"
            if not os.path.isfile(written):
                raise RuntimeError(f"Blender did not write {written}")
            import OpenImageIO as oiio
            sp = oiio.ImageInput.open(written)
            tw, th = sp.spec().width, sp.spec().height
            sp.close()
            if (tw, th) != (x1 - x0, y1 - y0):
                raise RuntimeError(f"tile came out {tw}x{th}, expected "
                                   f"{x1 - x0}x{y1 - y0}: border maths is off")
            os.replace(written, path)

            el = time.time() - t0
            times.append(el)
            done = mark_existing()
            manifest["frames_done"] = min(done, total - 1)
            manifest["frame_times"][os.path.basename(path)] = round(el, 2)
            try:
                manifest["last_file"] = _update_preview(tile_dir, tiles, W, H, vl_name)
            except Exception as e:
                print(f"         (preview not updated: {e})", flush=True)
            write_manifest()
            avg = sum(times) / len(times)
            print(f"         ↳  tile {_hms(el)}  |  avg {_hms(avg)}  |  "
                  f"elapsed {_hms(time.time() - t_session)}  |  "
                  f"ETA {_hms(avg * (total - done))}", flush=True)
    finally:
        for h in list(bpy.app.handlers.render_stats):
            if getattr(h, "__name__", "") == "_stats":
                bpy.app.handlers.render_stats.remove(h)
        if keep["media"] is not None:
            s.media_type = keep["media"]
        s.file_format = keep["fmt"]
        for k, a in (("depth", "color_depth"), ("codec", "exr_codec"), ("mode", "color_mode")):
            try:
                setattr(s, a, keep[k])
            except TypeError:
                pass
        r.use_border, r.use_crop_to_border = keep["border"], keep["crop"]
        r.use_compositing = keep["comp"]
        r.use_file_extension = keep["ext"]
        r.use_persistent_data = keep["persist"]
        if "dpass" in keep:
            scene.view_layers[vl_name].cycles.denoising_store_passes = keep["dpass"]
        (r.border_min_x, r.border_max_x, r.border_min_y, r.border_max_y) = keep["bx"]
        r.filepath = template

    missing = [os.path.basename(p) for _, p in tiles if not os.path.isfile(p)]
    if missing:
        raise RuntimeError(f"{len(missing)} tiles missing: {missing[:5]}")

    print(f"\n  All {total} tiles on disk. Stitching…", flush=True)
    t0 = time.time()
    _stitch(tiles, W, H, stitched)
    print(f"  Compositing the whole image with the scene's compositor…", flush=True)
    _composite(scene, stitched, final)
    print(f"  stitch + composite  {_hms(time.time() - t0)}", flush=True)

    # stitched.exr holds every tile, raw, so the tiles themselves are now
    # redundant. Kept: stitched.exr (to redo the compositing), layout.json,
    # preview.jpg. Only once the final image is safely on disk.
    if os.path.isfile(final):
        freed = 0
        for _, p in tiles:
            try:
                freed += os.path.getsize(p)
                os.remove(p)
            except OSError:
                pass
        print(f"  tiles removed ({freed / 1e6:.0f} MB), stitched.exr kept to "
              f"redo the compositing", flush=True)

    manifest["grid"] = "1" * total
    manifest["frames_done"] = total
    manifest["last_file"] = final
    write_manifest()
    print(f"\n{'═' * 64}\n  {total}/{total} tiles  |  final  →  {final}"
          f"\n  to redo the compositing: change it in the .blend, delete the"
          f"\n  final image, resume the job (nothing is re-rendered)\n{'═' * 64}",
          flush=True)
    return manifest


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

    if MODE == "still" and TILE_SIZE > 0 and not is_movie:
        try:
            return render_tiled_still(scene, frames[0], template, out_dir,
                                      manifest, old, write_manifest)
        finally:
            r.filepath   = state["filepath"]
            scene.camera = state["camera"]

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

        # Blender's own -f render refuses to overwrite an existing file and
        # exits 0 having done nothing — which reads as a successful render of
        # zero frames. The decision about whether to re-render belongs to
        # SKIP_EXISTING above, which has already been made by this point, so
        # Blender must not make it again and more quietly.
        r.use_overwrite = True
        r.use_placeholder = False

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
