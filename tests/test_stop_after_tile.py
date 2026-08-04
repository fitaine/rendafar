"""
"Stop now" versus "finish this tile first".

Stopping used to be one thing: kill immediately and lose the tile in flight.
On a 90s tile that is a shrug; on a slow scene it is real work thrown away for
no reason, when waiting a moment would have cost nothing.

The mechanism deliberately needs NOTHING from the render script. Every job is
pinned to a frozen dated script, so anything requiring a script change would
not work for jobs already queued against an older one — including the two 120k
renders sitting in the live queue right now, both pinned to the 2026-07-26
copy. Instead the runner watches the manifest, which the script flushes right
after writing each tile: when the count goes up, the tile is safely on disk.

    python tests\test_stop_after_tile.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="stop_after_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()
# Keep the test quick; the real defaults are minutes.
config.STOP_AFTER_MIN_S = 6
config.STOP_AFTER_MAX_S = 8

import paths                        # noqa: E402
import queue_manager as qm          # noqa: E402
import runner as runner_mod         # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


killed = []
runner_mod.kill_tree = lambda pid: killed.append(pid)

BLEND = os.path.join(TMP, "x.blend")
open(BLEND, "wb").write(b"x")

_done = {"n": 10}
paths.progress = lambda job: {"done": _done["n"], "total": 900, "unit": "tiles",
                              "percent": 1.1, "avg_tile_s": 1}


class FakeProc:
    def __init__(self):
        self.pid = 4242
        self._alive = True

    def poll(self):
        return None if self._alive else 0


def armed_runner():
    """A Runner that believes it is mid-render."""
    job = qm.create_job(BLEND, name="Bure")
    qm.set_state(job["id"], qm.RUNNING)
    r = runner_mod.Runner()
    r._proc = FakeProc()
    r._job_id = job["id"]
    r._started = time.time()
    killed.clear()
    _done["n"] = 10
    return r


print("\n=== 1. stop now is unchanged ===")
r = armed_runner()
check("stop() reports it stopped something", r.stop() is True)
check("...and kills straight away", killed == [4242], f"got {killed}")
check("...and is not in the waiting state", not r.stopping_after_tile())

print("\n=== 2. stop after the current tile waits ===")
r = armed_runner()
check("stop(after_current=True) reports it started a stop",
      r.stop(after_current=True) is True)
check("...and returns while the render is still going",
      killed == [], f"killed too early: {killed}")
check("...and says so, so the UI can explain the delay",
      r.stopping_after_tile() and r.status()["stopping_after_tile"])

time.sleep(3)
check("still rendering while the tile is unfinished",
      killed == [], f"got {killed}")

_done["n"] = 11                       # the tile lands
time.sleep(3.5)
check("kills once the manifest records the tile",
      killed == [4242], f"got {killed}")
check("...and clears the waiting flag", not r.stopping_after_tile())

print("\n=== 3. it gives up rather than waiting for ever ===")
r = armed_runner()
r.stop(after_current=True)
time.sleep(config.STOP_AFTER_MAX_S + 3)      # tile never lands
check("a wedged render is stopped anyway once the budget runs out",
      killed == [4242], f"got {killed}")
check("...and stops waiting", not r.stopping_after_tile())

print("\n=== 4. changing your mind ===")
r = armed_runner()
r.stop(after_current=True)
time.sleep(0.5)
check("cancel_stop_after() reports it cancelled", r.cancel_stop_after() is True)
check("...and clears the flag immediately", not r.stopping_after_tile())
_done["n"] = 12
time.sleep(3.5)
check("...and nothing is killed afterwards", killed == [], f"got {killed}")
check("cancelling when not waiting is a harmless no-op",
      r.cancel_stop_after() is False)

print("\n=== 5. the render finishing on its own ===")
r = armed_runner()
r.stop(after_current=True)
time.sleep(0.5)
r._proc._alive = False               # Blender exits by itself
time.sleep(3)
check("no kill is issued at a process that already ended",
      killed == [], f"got {killed}")
check("...and the waiting flag clears", not r.stopping_after_tile())

print("\n=== 6. guards ===")
r = runner_mod.Runner()              # nothing running
check("stopping when idle reports nothing to stop", r.stop() is False)
check("...and the same with after_current", r.stop(after_current=True) is False)

r = armed_runner()
r.stop(after_current=True)
r.stop(after_current=True)           # pressed twice
time.sleep(0.3)
_done["n"] = 11
time.sleep(3.5)
check("asking twice does not stack watchers or double-kill",
      killed == [4242], f"one kill expected, got {killed}")

r = armed_runner()
r.stop(after_current=True)
time.sleep(0.5)
check("pressing Stop again while waiting is accepted", r.stop() is True,
      "the 'already waiting' short-circuit used to swallow this, so a second "
      "press did nothing at all")
check("...and kills without waiting for the tile",
      killed == [4242], f"got {killed}")
check("...and leaves no watcher behind to kill twice",
      not r.stopping_after_tile())
_done["n"] = 11
time.sleep(3.5)
check("...even after the tile would have landed",
      killed == [4242], f"got {killed}")

print("\n=== 7. the phone offers the same choice ===")
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(APP, "static", "index.html"), encoding="utf-8").read()
start = html.index("function actions(s)")
block = html[start:html.index("\n}", start)]      # the whole function, however it grows

check("the phone offers 'finish tile, then stop' while rendering",
      "after_current:true" in block,
      "two buttons rather than a dialog: on a phone the safe option should be "
      "one tap, not a tap plus a choice")
check("...with a plain immediate stop beside it", "act('stop')" in block)
check("...and says so while waiting, rather than looking ignored",
      "stopping_after_tile" in block and "Finishing tile" in block)
check("act() forwards the extra body field",
      "Object.assign(body, extra)" in html,
      "checked live in a browser too: /api/stop received {\"after_current\":true}")

# index.html is re-read on every request, so a phone can be running today's
# HTML against a server process started days ago — which is the live situation
# right now, mid-render. An older server ignores after_current and kills
# immediately, so offering the button there would promise the opposite of what
# happens.
check("the offer is gated on the server actually supporting it",
      'const canWait = "stopping_after_tile" in s' in block,
      "absent on old servers, false on new ones — presence is the probe")
check("...falling back to a plain Stop when it does not",
      block.count("act('stop')") >= 2 and "Stop render" in block,
      "verified in a browser: a status with no stopping_after_tile field "
      "renders only 'Stop render'")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
