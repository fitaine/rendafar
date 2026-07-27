"""
GPU telemetry from nvidia-smi.

Why this matters more than it looks: NOTES.md documents a failure where a scene
went memory-bound and the GPU drew a fraction of its rated power while the
render crawled.  Nothing else on screen reveals that — tiles keep appearing,
just far too slowly.  Power draw as a share of the card's limit is the tell.
"""

import os
import shutil
import subprocess
import threading
import time

# nvidia-smi is not on PATH on this machine; it lives in System32.
_CANDIDATES = [
    r"C:\Windows\System32\nvidia-smi.exe",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
]

_FIELDS = ("name,power.draw,power.limit,temperature.gpu,"
           "memory.used,memory.total,utilization.gpu")

# Below this share of the power limit while rendering, the GPU is stalling on
# memory rather than doing maths.  See the memory-bound note in NOTES.md.
LOW_POWER_FRACTION = 0.45

# A tile this slow is a real render, not a preview or a light test scene.
SLOW_TILE_S = 90

_cache      = {"at": 0.0, "data": None}
_cache_lock = threading.Lock()
CACHE_S     = 2.0

# Launched from the desktop icon the server has no console, so every subprocess
# would pop a new console window for a fraction of a second. Polling nvidia-smi
# every couple of seconds made the screen flash constantly.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _exe():
    for p in _CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("nvidia-smi")


def _query():
    exe = _exe()
    if not exe:
        return {"available": False, "error": "nvidia-smi not found"}
    try:
        out = subprocess.run(
            [exe, f"--query-gpu={_FIELDS}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"available": False, "error": str(e)}
    line = (out.stdout or "").strip().splitlines()
    if not line:
        return {"available": False, "error": (out.stderr or "no output").strip()[:120]}

    parts = [p.strip() for p in line[0].split(",")]
    if len(parts) < 7:
        return {"available": False, "error": "unexpected nvidia-smi output"}

    def num(x):
        try:
            return float(x)
        except ValueError:
            return None

    power, limit = num(parts[1]), num(parts[2])
    used,  total = num(parts[4]), num(parts[5])
    return {
        "available":   True,
        "name":        parts[0],
        "power_w":     power,
        "power_limit": limit,
        "power_pct":   round(100 * power / limit) if power and limit else None,
        "temp_c":      num(parts[3]),
        "vram_used":   used,
        "vram_total":  total,
        "vram_pct":    round(100 * used / total) if used and total else None,
        "util_pct":    num(parts[6]),
    }


def read(rendering=False, avg_tile_s=None):
    """
    Current GPU state, cached briefly so rapid polling can't spawn processes.

    The memory-bound warning needs BOTH conditions to be meaningful:

      * `rendering` — an idle card draws almost nothing, and that is healthy.
      * slow tiles  — a light scene legitimately finishes tiles in under a
        second without ever loading the GPU. Low power there is fine. The
        documented failure is low power *while tiles crawl*, which is what
        memory-bound looks like: cores stalling on VRAM instead of computing.

    Warning on low power alone cries wolf on every preview and light scene.
    """
    with _cache_lock:
        now = time.time()
        if _cache["data"] and now - _cache["at"] < CACHE_S:
            data = dict(_cache["data"])
        else:
            data = _query()
            _cache["at"], _cache["data"] = now, data
            data = dict(data)

    data["warning"] = None
    if (rendering
            and data.get("available")
            and data.get("power_pct") is not None
            and data["power_pct"] < LOW_POWER_FRACTION * 100
            and avg_tile_s is not None
            and avg_tile_s >= SLOW_TILE_S):
        data["warning"] = (
            f"GPU at {data['power_pct']}% of its power limit while tiles take "
            f"{round(avg_tile_s / 60)} min — the scene is likely memory-bound "
            f"(BVH overflowing VRAM), not compute-bound. Check point count and "
            f"ball radius."
        )
    return data
