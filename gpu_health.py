"""
GPU health: catch a hung or crashed NVIDIA driver before it wastes a night.

What happened on 2026-11-12 (Château Julien, tiled still):
  15:52  OptiX "illegal memory access" (CUDA 700) killed Blender on tile 10.
         Windows logged nvlddmkm event 153: the driver itself had failed.
  15:56  The job was relaunched on the same driver session, no reboot.
  17:53  Still on tile 10 after 2 h (tiles took 20-41 min). The card reported
         99 % load but drew 57 W of 200 W at 41 °C: busy-looking, doing
         nothing. Only a reboot resets the driver after this.

So two guards:

1. crash_in_log(): a Blender exit whose output carries a CUDA/OptiX device
   fault means the driver is suspect. The job is re-queued (finished tiles are
   kept) and the queue refuses to start anything until Windows has rebooted.

2. HangWatch: a hang is declared when Cycles' sample count has not moved for
   GPU_STALL_S. Power is only logged: Tête du faisan renders healthily at
   ~65 W / 100 %, the same reading as the Julien hang, so power cannot decide.
   Scripts that report no samples get only a 6 h / 10x-average ceiling.
"""

import ctypes
import os
import re
import subprocess
import time

import config

NVSMI = r"C:\Windows\System32\nvidia-smi.exe"   # PATH loses System32 on this machine

# Defaults; any can be overridden in config.py.
# A hang = Cycles' sample count frozen this long (render_classic 11-12b+ reports it).
GPU_STALL_S           = getattr(config, "GPU_STALL_S", 30 * 60)
# Scripts that report no samples: only a very generous per-tile ceiling.
GPU_BLIND_MIN_TILE_S  = getattr(config, "GPU_BLIND_MIN_TILE_S", 6 * 3600)
GPU_BLIND_TILE_FACTOR = getattr(config, "GPU_BLIND_TILE_FACTOR", 10.0)
GPU_SAMPLE_S         = getattr(config, "GPU_SAMPLE_S", 30)
GPU_LOG_EVERY_S      = getattr(config, "GPU_LOG_EVERY_S", 10 * 60)

_CRASH = re.compile(r"illegal memory access|CUDA error code: 7\d\d|OPTIX_ERROR_CUDA_ERROR"
                    r"|CUDA_ERROR_LAUNCH_FAILED|unspecified launch failure"
                    r"|device kernel launch|ERROR_DEVICE_LOST", re.I)


def crash_in_log(lines):
    """The first line showing a GPU device fault, or None."""
    for line in lines:
        if _CRASH.search(line):
            return line.strip()[:200]
    return None


def boot_time():
    """When Windows last booted (epoch s, rounded to the minute)."""
    try:
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        up = ctypes.windll.kernel32.GetTickCount64()
        return round((time.time() - up / 1000) / 60) * 60
    except Exception:
        return None


def sample():
    """{'util': %, 'power': W, 'limit': W, 'temp': C} or None."""
    if not os.path.isfile(NVSMI):
        return None
    try:
        out = subprocess.run(
            [NVSMI, "--query-gpu=utilization.gpu,power.draw,power.limit,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        u, p, l, t = [float(x) for x in out.strip().splitlines()[0].split(",")]
        return {"util": u, "power": p, "limit": l, "temp": t}
    except Exception:
        return None


# ── reboot gate ──────────────────────────────────────────────────────────────

def mark_needs_reboot(state, reason):
    state["gpu_needs_reboot"] = {"reason": reason, "boot": boot_time(),
                                 "at": time.strftime("%Y-%m-%d %H:%M")}


def reboot_block(state):
    """Reason string while a reboot is still owed, else None (and clears it)."""
    flag = state.get("gpu_needs_reboot")
    if not flag:
        return None
    now = boot_time()
    if now is not None and flag.get("boot") is not None and abs(now - flag["boot"]) > 120:
        state["gpu_needs_reboot"] = None        # rebooted since: driver is fresh
        return None
    return flag.get("reason") or "GPU fault"


# ── hang watch ───────────────────────────────────────────────────────────────

class HangWatch:
    """
    Call tick(tile_age_s, avg_tile_s) every GPU_SAMPLE_S. Returns a reason
    string once a hang is established, else None. log(line) receives the
    periodic power readings.
    """

    def __init__(self, log):
        self.log = log
        self.low_since = None
        self.last_log = 0

    def tick(self, tile_age_s, avg_tile_s, progress_age_s=None):
        """
        progress_age_s: seconds since Cycles' sample count last moved, or None
        when the render reports no samples (older pinned scripts).

        Power is logged, NOT used to decide: Tête du faisan renders healthily
        at ~65 W / 100 % (measured 2026-11-12), the same reading the Julien
        hang showed. Only a frozen sample count proves a hang.
        """
        s = sample()
        now = time.time()
        if s and now - self.last_log >= GPU_LOG_EVERY_S:
            self.last_log = now
            self.log(f"  [gpu] {s['util']:.0f} %  {s['power']:.0f}/{s['limit']:.0f} W  "
                     f"{s['temp']:.0f} °C   tile running {tile_age_s / 60:.0f} min")
        if progress_age_s is not None:
            if progress_age_s >= GPU_STALL_S:
                return (f"GPU hung: no new sample for {progress_age_s / 60:.0f} min "
                        f"(tile running {tile_age_s / 60:.0f} min)")
            return None
        # No sample reporting: only a very generous ceiling.
        limit = max(GPU_BLIND_MIN_TILE_S, GPU_BLIND_TILE_FACTOR * (avg_tile_s or 0))
        if tile_age_s > limit:
            return (f"GPU hung? tile running {tile_age_s / 3600:.1f} h with no "
                    f"progress reporting (avg {(avg_tile_s or 0) / 60:.0f} min)")
        return None
