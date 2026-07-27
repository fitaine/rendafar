"""
What a tiled render will actually be: total pixels, tile shape, grid size.

The numbers are computed by **the job's own pinned render script**, not by a
copy of its maths living here. compute_grid() is lifted out of that file with
ast and run as-is.

That matters more than it looks. The tile short-edge is derived from the image
aspect so that N_cols == N_rows, which the script's own docstring says is
required for the single-focal-length zoom to work on both axes. Re-implementing
that here would create exactly the drift the whole frozen-snapshot scheme
exists to prevent — and the failure would be misaligned tiles, discovered
after 45 hours of rendering.

Lifting the function also means the preview follows whichever snapshot a job
pinned: an older job previews with the maths it will actually run.
"""

import ast
import os

_cache = {}      # script path -> (mtime, callable)


def _load_compute_grid(script_path):
    """
    Pull compute_grid() out of a render script and return it as a callable.

    The script cannot simply be imported: it does `import bpy` at module level
    and only exists inside Blender. compute_grid itself needs nothing but math.
    """
    key = os.path.abspath(script_path)
    try:
        mtime = os.path.getmtime(key)
    except OSError as e:
        raise FileNotFoundError(f"render script not found: {key}") from e

    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]

    with open(key, encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src)
    node = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "compute_grid"),
                None)
    if node is None:
        raise ValueError(f"compute_grid() not found in {os.path.basename(key)}")

    import math
    ns = {"math": math}
    exec(compile(ast.Module(body=[node], type_ignores=[]), key, "exec"), ns)
    fn = ns["compute_grid"]
    _cache[key] = (mtime, fn)
    return fn


def preview(script_path, blend_info, long_edge, tile_long,
            total_w=0, total_h=0):
    """
    The grid a tiled job would produce.

    `blend_info` is the dict from blend_info.read(): the scene resolution and
    pixel aspect it carries are what the script reads from the .blend.
    """
    try:
        compute_grid = _load_compute_grid(script_path)
    except (OSError, ValueError) as e:
        return {"error": str(e)}

    if not blend_info or blend_info.get("error"):
        return {"error": "the .blend's resolution is not known yet"}

    try:
        total_W, total_H, tile_W, tile_H, cols, rows = compute_grid(
            int(blend_info.get("resolution_x") or 1920),
            int(blend_info.get("resolution_y") or 1080),
            float(blend_info.get("pixel_aspect_x") or 1.0),
            float(blend_info.get("pixel_aspect_y") or 1.0),
            int(long_edge), int(tile_long),
            override_w=int(total_w or 0), override_h=int(total_h or 0),
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    return {
        "total_w": total_W, "total_h": total_H,
        "tile_w": tile_W, "tile_h": tile_H,
        "cols": cols, "rows": rows,
        "tiles": cols * rows,
        "megapixels": round(total_W * total_H / 1_000_000),
        "square_tiles": tile_W == tile_H,
        "script": os.path.basename(script_path),
    }
