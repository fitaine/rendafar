r"""
Server-side file browsing, so the UI can pick a .blend.

Why this exists: a browser cannot tell you where a file lives. `<input
type=file>` and drag-and-drop onto a web page both hand over the *contents* and
a bare filename, never `D:\scenes\valley.blend` — deliberate sandboxing. But
Rendafar needs the path, because Blender opens the file in place and never
copies it.

So the picking happens on the server. It also means the picker works from the
phone, which a native file dialog never could.

Security: this lists directories to anyone who reaches the app. That is
acceptable only because the app already starts processes on this machine —
anyone through the door owns it regardless — and because it is never exposed
without Cloudflare Access in front. It is read-only: no opening, no writing, no
deleting.
"""

import os
import string

BLEND_EXT = ".blend"

# Folders that are pure noise when hunting for a .blend.
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows", "node_modules",
    "__pycache__", ".git", "appdata", "programdata",
}


def drives():
    """
    Drive letters that currently exist.

    Uses GetLogicalDrives (a bitmask, instant) rather than testing each letter
    with os.path.exists: probing A: and B: makes Windows query phantom floppy
    devices, which blocks for seconds and hung the whole request.
    """
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase)
                if mask & (1 << i)]
    except Exception:
        # Not Windows, or the call failed: fall back, skipping the floppy slots.
        return [f"{c}:\\" for c in string.ascii_uppercase[2:]
                if os.path.exists(f"{c}:\\")]


def listing(path=None):
    """
    Folders and .blend files at `path`.

    `path` None or empty lists the drives plus a few useful starting points.
    Unreadable folders return an error rather than raising, since half of
    C:\\ is unreadable to a normal user and that is not a failure.
    """
    if not path:
        entries = [{"name": d, "path": d, "dir": True} for d in drives()]
        for label in ("Desktop", "Documents", "Downloads"):
            p = os.path.join(os.path.expanduser("~"), label)
            if os.path.isdir(p):
                entries.insert(0, {"name": label, "path": p, "dir": True})
        return {"path": "", "parent": None, "entries": entries}

    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return {"error": f"not a folder: {path}"}

    dirs, files = [], []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir():
                        if e.name.lower() in SKIP_DIRS or e.name.startswith("."):
                            continue
                        dirs.append({"name": e.name, "path": e.path, "dir": True})
                    elif e.name.lower().endswith(BLEND_EXT):
                        files.append({
                            "name": e.name,
                            "path": e.path,
                            "dir": False,
                            "mb": round(e.stat().st_size / (1024 * 1024), 1),
                            "mtime": e.stat().st_mtime,
                        })
                except OSError:
                    continue          # a single unreadable entry is not fatal
    except PermissionError:
        return {"error": "no permission to read this folder", "path": path,
                "parent": os.path.dirname(path) or None, "entries": []}
    except OSError as e:
        return {"error": str(e), "path": path,
                "parent": os.path.dirname(path) or None, "entries": []}

    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["mtime"], reverse=True)   # newest .blend first

    parent = os.path.dirname(path.rstrip("\\/"))
    if parent == path:
        parent = ""                    # at a drive root, go back to the drive list

    return {"path": path, "parent": parent, "entries": dirs + files}
