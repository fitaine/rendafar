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

def run(minutes, avg_min, util, power, stall_after_min=None):
    """Tick for `minutes`; samples move every tick until stall_after_min."""
    feed.update(util=util, power=power, limit=200, temp=41)
    out, last_move = None, 0.0
    for k in range(int(minutes * 2)):
        clock[0] += 30
        if stall_after_min is None or clock[0] < stall_after_min * 60:
            last_move = clock[0]
        out = w.tick(clock[0], avg_min * 60, clock[0] - last_move) or out
    return out

check("Tete du faisan: 65 W for 3 h with samples moving is NOT a hang",
      run(180, 8.5, 100, 65) is None)
clock[0] = 0; w = g.HangWatch(logged.append)
r = run(120, 30, 99, 57, stall_after_min=5)
check("samples frozen for 30 min is a hang", r and "no new sample" in r, r)
clock[0] = 0; w = g.HangWatch(logged.append)
check("samples frozen 20 min: not yet", run(25, 30, 99, 57, stall_after_min=5) is None)
clock[0] = 0; w = g.HangWatch(logged.append)
feed.update(util=99, power=57, limit=200, temp=41)
blind = None
for _ in range(int(7 * 3600 / 30)):
    clock[0] += 30
    blind = w.tick(clock[0], 30 * 60, None) or blind
check("no sample reporting: only the 6 h ceiling", blind and clock[0] >= 6 * 3600, blind)
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
