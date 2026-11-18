"""
finish_agx.py: AgX for a linear EXR, outside Blender's compositor.

Run with Blender 5.0 (it brings OpenColorIO and the AgX config):

    blender -b --factory-startup --python finish_agx.py -- <image.exr> [scene.blend]

Paths with accents break Blender's argv on Windows, so they can also come from
the environment: FINISH_EXR=..., FINISH_BLEND=...

Input: a plain RGBA float EXR in Linear Rec.709 (stitched_photoshop.exr, or
the same file after glare was added in Photoshop and saved as 32-bit EXR).

Writes next to it:
  <name>_agx.tif      16-bit sRGB, the image as Blender shows it (AgX view,
                      plus the scene's Look / Exposure / Gamma if a .blend is given)
  <name>_agxlog.tif   16-bit AgX Log: flat, nothing clipped, for adjusting in Ps
  agxlog_to_srgb.cube 65^3 LUT AgX Log -> AgX sRGB (with the same Look), to put
                      on top of the log TIFF as a Color Lookup layer

Why not AgX straight in Photoshop: Ps has no OCIO and its LUTs clip above 1.0,
so the linear EXR can't go through a LUT. The log TIFF holds the whole range in
0..1, where a LUT works.
"""
import os
import sys

import bpy
import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as ocio

CUBE_N = 65


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    exr = os.environ.get("FINISH_EXR") or (a[0] if a else "")
    blend = os.environ.get("FINISH_BLEND") or (a[1] if len(a) > 1 else "")
    if not exr or not os.path.isfile(exr):
        raise SystemExit(f"no input EXR: {exr!r}")
    return exr, blend


def scene_view(blend):
    """The scene's own view settings, so the result matches its render."""
    v = {"view": "AgX", "look": "None", "exposure": 0.0, "gamma": 1.0}
    if blend and os.path.isfile(blend):
        bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False)
        vs = bpy.context.scene.view_settings
        v = {"view": vs.view_transform, "look": vs.look,
             "exposure": vs.exposure, "gamma": vs.gamma}
    print(f"  view {v['view']!r}  look {v['look']!r}  exposure {v['exposure']}  "
          f"gamma {v['gamma']}", flush=True)
    if v["view"] != "AgX":
        print("  (scene view is not AgX: using AgX anyway)", flush=True)
    return v


def ocio_config():
    root = os.path.dirname(bpy.app.binary_path)
    ver = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
    return ocio.Config.CreateFromFile(
        os.path.join(root, ver, "datafiles", "colormanagement", "config.ocio"))


def look_name(cfg, look):
    """Blender shows 'AgX - Punchy'; the OCIO look may be named either way."""
    if not look or look == "None":
        return ""
    names = [l.getName() for l in cfg.getLooks()]
    for cand in (look, look.split(" - ", 1)[-1]):
        if cand in names:
            return cand
    print(f"  (look {look!r} not found in OCIO config, ignored)", flush=True)
    return ""


def display_proc(cfg, src, look):
    t = ocio.DisplayViewTransform(src=src, display="sRGB", view="AgX")
    if look:
        t.setLooksBypass(False)
        vp = ocio.LegacyViewingPipeline()
        vp.setDisplayViewTransform(t)
        vp.setLooksOverrideEnabled(True)
        vp.setLooksOverride(look)
        return vp.getProcessor(cfg).getDefaultCPUProcessor()
    return cfg.getProcessor(t).getDefaultCPUProcessor()


def apply(cpu, rgb):
    a = np.ascontiguousarray(rgb, dtype=np.float32)
    cpu.applyRGB(a)
    return a


def write_tif(path, rgb, alpha):
    h, w = rgb.shape[:2]
    data = np.concatenate([np.clip(rgb, 0, 1), alpha[..., None]], axis=2)
    spec = oiio.ImageSpec(w, h, 4, oiio.UINT16)
    spec.attribute("compression", "zip")
    out = oiio.ImageOutput.create(path)
    if not out or not out.open(path, spec):
        raise RuntimeError(f"cannot write {path}: {oiio.geterror()}")
    out.write_image(data.astype(np.float32))
    out.close()
    print(f"  wrote {path}", flush=True)


def write_cube(path, cpu):
    n = CUBE_N
    g = np.linspace(0, 1, n, dtype=np.float32)
    # .cube order: red changes fastest
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    rgb = np.stack([r, gg, b], -1).reshape(-1, 3)
    rgb = apply(cpu, rgb)
    with open(path, "w", encoding="ascii") as f:
        f.write(f"TITLE \"AgX Log to AgX sRGB\"\nLUT_3D_SIZE {n}\n")
        for p in np.clip(rgb, 0, 1):
            f.write(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")
    print(f"  wrote {path}", flush=True)


def main():
    exr, blend = args()
    v = scene_view(blend)
    cfg = ocio_config()
    look = look_name(cfg, v["look"])

    buf = oiio.ImageBuf(exr)
    px = buf.get_pixels(oiio.FLOAT)
    names = list(buf.spec().channelnames)
    comb = [next((i for i, n in enumerate(names) if n.endswith(f"Combined.{c}")), None)
            for c in "RGBA"]
    if None not in comb[:3]:          # a stitched.exr: take the Combined pass
        px = px[..., [i for i in comb if i is not None]]
    if px.shape[2] < 3:
        raise SystemExit("needs RGB")
    rgb = px[..., :3] * (2.0 ** v["exposure"])
    alpha = px[..., 3] if px.shape[2] > 3 else np.ones(px.shape[:2], np.float32)
    h, w = rgb.shape[:2]
    print(f"  {os.path.basename(exr)}  {w} x {h}", flush=True)

    stem = os.path.splitext(exr)[0]
    flat = rgb.reshape(-1, 3)

    # final, as Blender displays it
    disp = apply(display_proc(cfg, "Linear Rec.709", look), flat).reshape(h, w, 3)
    if v["gamma"] != 1.0:
        disp = np.clip(disp, 0, None) ** (1.0 / v["gamma"])
    write_tif(stem + "_agx.tif", disp, alpha)

    # log version + the LUT that turns it into the same final
    to_log = cfg.getProcessor("Linear Rec.709", "AgX Log").getDefaultCPUProcessor()
    log = apply(to_log, flat).reshape(h, w, 3)
    write_tif(stem + "_agxlog.tif", log, alpha)
    write_cube(os.path.join(os.path.dirname(exr), "agxlog_to_srgb.cube"),
               display_proc(cfg, "AgX Log", look))

    # proof: log -> LUT path must match the direct path
    back = apply(display_proc(cfg, "AgX Log", look), log.reshape(-1, 3)).reshape(h, w, 3)
    err = float(np.abs(np.clip(back, 0, 1) - np.clip(disp if v["gamma"] == 1.0 else back, 0, 1)).max())
    print(f"  log path vs direct: max difference {err:.4f}", flush=True)


main()
