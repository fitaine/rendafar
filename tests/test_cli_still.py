"""
Stills render through Blender's own loop, so their progress reaches the log.

bpy.ops.render.render() prints NOTHING in background mode. Blender's own render
loop — the one "-f 1" starts — prints a line per pass, with its own ETA:

    00:13.390 render | Fra:1 | Remaining: 00:12.34 | Mem: 12M | Sample 1025/2000

Measured on one scene, same settings, same 27s render:

    script's own loop   0 Sample lines, 0 Remaining
    Blender's own loop  3 Sample lines, 1 Remaining

runner.py has always known how to parse that line. It simply never had one.

The dangerous case, and the reason for the guard tested here: passing "-f" to a
job pinned to an OLDER script would render the frame twice, once in the
script's loop and once in Blender's.

    python tests\test_cli_still.py
"""

import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

import config                       # noqa: E402
from runner import cli_still_frame, parse_progress_line   # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


NEW = os.path.join(APP, "frozen", "render_classic_2026-08-10_cli.py")
OLD = os.path.join(APP, "frozen", "render_classic_2026-08-07.py")
TILED = os.path.join(APP, "frozen", "render_tiles_unlimited_2026-07-26.py")


def job(script=NEW, **cfg):
    c = {"MODE": "still", "FRAME": 1}
    c.update(cfg)
    return {"type": "classic", "blend": "x.blend", "script": script, "config": c}


print("\n=== 1. when Blender is allowed to drive ===")
check("a still with a known frame and a script that understands it",
      cli_still_frame(job()) == 1, str(cli_still_frame(job())))

print("\n=== 2. the guard against rendering twice ===")
check("a job pinned to an OLDER script is left alone",
      cli_still_frame(job(script=OLD)) is None,
      "that script runs its own loop, so adding -f would render the frame "
      "twice — once by the script and once by Blender")
check("...and so is a job whose script has vanished",
      cli_still_frame(job(script="no_such_file.py")) is None)

print("\n=== 3. only stills ===")
check("an animation keeps the script's loop",
      cli_still_frame(job(MODE="animation")) is None,
      "which frames it still owes depends on what is on disk, and is not "
      "known until the .blend is open")
check("a tiled job is untouched",
      cli_still_frame({"type": "tiled", "script": TILED, "config": {}}) is None)
check("a still with no frame number does not guess",
      cli_still_frame(job(FRAME=0)) is None,
      "-f renders exactly what it is told; the wrong frame wastes an hour, "
      "while a missing progress bar is a nuisance")

print("\n=== 4. the line Blender prints is one the runner already reads ===")
live = parse_progress_line(
    "00:13.390  render | Fra: 1 | Remaining: 00:12.34 | Mem: 12M | Sample 1025/2000")
check("the sample count comes through",
      live and live["sample"] == 1025 and live["samples_total"] == 2000, str(live))
check("...and Blender's own ETA", live.get("remaining") == "00:12.34", str(live))
check("...and a percentage for the bar", live.get("percent") == 51.2, str(live))
check("our own log lines are not mistaken for progress",
      parse_progress_line("  [    1/1]  frame 1  — rendering, progress below")
      is None)

print("\n=== 5. the script holds up its end ===")
src = open(NEW, encoding="utf-8").read()
check("the new script recognises the handshake",
      'RENDAFAR_CLI_RENDER") == "1"' in src)
check("...and returns without rendering when it is set",
      "bpy.app.handlers.render_write.append" in src,
      "the bookkeeping moves to handlers; Blender does the render")
check("...but only for a single frame, never a movie",
      "and not is_movie" in src)
check("it still honours SKIP_EXISTING",
      "already on disk" in src,
      "a still that is already rendered must not be redone just because the "
      "render moved to Blender's loop")

check("the OLD script has no such handshake, which is what makes it safe",
      "RENDAFAR_CLI_RENDER" not in open(OLD, encoding="utf-8").read())

print("\n=== 6. the way back is one line ===")
cfg_src = open(os.path.join(APP, "config.py"), encoding="utf-8").read()
check("config points at the new script",
      "render_classic_2026-08-10_cli.py" in cfg_src)
check("...and says how to undo it",
      "render_classic_2026-08-07.py" in cfg_src,
      "jobs pin their script, so switching back affects nothing already queued")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
