"""
GPU crash / hang guards (gpu_health), without a GPU fault to hand.

    python tests\\test_gpu_health.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gpu_health as g

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


# 1. Crash detection on the real 2026-11-12 Château Julien log.
log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "job_0074.log")
if os.path.isfile(log):
    lines = open(log, encoding="utf-8", errors="replace").read().splitlines()
    check("CUDA 700 crash found in the Julien log", g.crash_in_log(lines))
check("normal render output is not a crash",
      g.crash_in_log(["Fra:1 Mem:4.2G | Sample 512/2048", "Saved: tile.exr"]) is None)

# 2. Live nvidia-smi read.
s = g.sample()
check("nvidia-smi sample readable", s and s["limit"] > 0, s)

import time as _t
b = g.boot_time()
check("boot time readable and in the past", b and 0 < _t.time() - b < 90 * 86400, b)

# 3. Hang logic, with a scripted card. Time is faked through g.time.
clock = [0.0]
g.time = type("T", (), {"time": staticmethod(lambda: clock[0]),
                        "strftime": staticmethod(lambda *a: "now")})
feed = {}
g.sample = lambda: dict(feed)
logged = []
w = g.HangWatch(logged.append)

def run(minutes, tile_start_min, avg_min, util, power):
    feed.update(util=util, power=power, limit=200, temp=41)
    out = None
    for _ in range(int(minutes * 2)):
        clock[0] += 30
        out = w.tick(clock[0] - tile_start_min * 60, avg_min * 60) or out
    return out

check("heavy tile at full power is never killed", run(180, 0, 30, 99, 180) is None)
clock[0] = 0; w = g.HangWatch(logged.append)
check("low power but tile still young: no kill", run(50, 0, 30, 99, 57) is None)
clock[0] = 0; w = g.HangWatch(logged.append)
r = run(120, 0, 30, 99, 57)
check("the 2026-11-12 picture (99 %, 57 W, 2 h on a 31-min tile) is a hang", r, r)
clock[0] = 0; w = g.HangWatch(logged.append)
check("idle card (low util) is not a hang", run(120, 0, 30, 5, 20) is None)
check("power readings go to the log", any("[gpu]" in l for l in logged), logged[:1])

# 4. Reboot gate.
real_boot = g.boot_time
g.boot_time = lambda: 1000
st = {}
g.mark_needs_reboot(st, "GPU driver crashed")
check("blocked on the same boot", g.reboot_block(st) == "GPU driver crashed")
g.boot_time = lambda: 1000 + 3600
check("cleared after a reboot", g.reboot_block(st) is None and st["gpu_needs_reboot"] is None)
g.boot_time = real_boot

print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
