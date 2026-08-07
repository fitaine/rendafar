"""
"queued" as the state the queue actually runs on, and the missing verb.

Reported live: "last time a render finished the next one did not follow
automatically". The auto-advance was never broken — start_next() only ever
picks a job whose state is literally `queued`, and there was nothing in that
state to pick. Both times a render finished here, the other jobs were sitting
`stopped`:

    job_0001 done 2026-07-30 09:25  ->  job_0002 stopped, job_0003 stopped
    job_0003 done 2026-08-01 13:44  ->  job_0002 stopped

A stopped job is parked, not waiting. Nothing in the UI could put one back
into `queued` without also starting it immediately, so the queue was always
empty by the time it mattered. enqueue() is that missing verb.

    python tests\test_queue_state.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="queue_state_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.ensure_dirs()

import queue_manager as qm          # noqa: E402
from controller import Controller   # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


BLEND = os.path.join(TMP, "x.blend")
open(BLEND, "wb").write(b"x")


def fresh(*states):
    """A queue of jobs in the given states, in order."""
    for f in os.listdir(config.QUEUE_DIR):
        os.remove(os.path.join(config.QUEUE_DIR, f))
    qm.save_state({"order": [], "running": None, "running_pid": None})
    jobs = []
    for i, st in enumerate(states):
        j = qm.create_job(BLEND, name=f"job {i}")
        if st != qm.QUEUED:
            qm.set_state(j["id"], st)
        jobs.append(qm.load_job(j["id"]))
    return jobs


print("\n=== 1. why nothing followed ===")
a, b = fresh(qm.QUEUED, qm.STOPPED)
check("a stopped job is not what next_queued() looks for",
      qm.next_queued()["id"] == a["id"],
      "it skips 'stopped' entirely — that is the whole bug")

a, b = fresh(qm.STOPPED, qm.STOPPED)
check("with everything stopped, there is nothing to advance TO",
      qm.next_queued() is None,
      "exactly the state the queue was in both times a render finished")

print("\n=== 2. enqueue: waiting, not started ===")
a, b = fresh(qm.QUEUED, qm.STOPPED)
qm.enqueue(b["id"])
check("enqueue puts a stopped job into 'queued'",
      qm.load_job(b["id"])["state"] == qm.QUEUED)
check("...and the queue now has something to follow with",
      [j["id"] for j in qm.all_jobs() if j["state"] == qm.QUEUED] ==
      [a["id"], b["id"]])
check("...without starting anything",
      qm.load_state().get("running") is None,
      "Queue means wait your turn, not go now")

print("\n=== 3. enqueue keeps its place; resume jumps the line ===")
a, b, c = fresh(qm.QUEUED, qm.QUEUED, qm.STOPPED)
before = list(qm.load_state()["order"])
qm.enqueue(c["id"])
check("enqueue leaves queue order untouched",
      qm.load_state()["order"] == before,
      f"order is what you set with the arrows.  {qm.load_state()['order']}")
check("...so the job that was already first still runs first",
      qm.next_queued()["id"] == a["id"])

a, b, c = fresh(qm.QUEUED, qm.QUEUED, qm.STOPPED)
qm.resume(c["id"])
check("resume DOES jump to the front, deliberately",
      qm.load_state()["order"][0] == c["id"],
      "that is right for power-cut recovery, where the interrupted job "
      "should come back first — which is why enqueue is a separate verb")

print("\n=== 4. enqueue refuses what it should ===")
a, = fresh(qm.DONE)
try:
    qm.enqueue(a["id"])
    check("a done job cannot be re-queued", False, "no error raised")
except ValueError:
    check("a done job cannot be re-queued", True,
          "re-rendering finished tiles is not a side effect of one click")

a, = fresh(qm.RUNNING)
try:
    qm.enqueue(a["id"])
    check("a running job cannot be re-queued", False, "no error raised")
except ValueError:
    check("a running job cannot be re-queued", True)

a, = fresh(qm.QUEUED)
check("queueing an already-queued job is a harmless no-op",
      qm.enqueue(a["id"])["state"] == qm.QUEUED)

print("\n=== 5. the queue advances on its own ===")
# The mechanism that was blamed. It works — it just needs something queued.
started = []


class Controller_(Controller):
    def __init__(self):
        super().__init__()
        self.runner.start = lambda jid: (started.append(jid),
                                         qm.set_state(jid, qm.RUNNING))[1]
        self.runner.is_busy = lambda: False
        self.notify = lambda *a, **k: None


a, b = fresh(qm.QUEUED, qm.STOPPED)
qm.set_state(a["id"], qm.RUNNING)
qm.enqueue(b["id"])
c = Controller_()
started.clear()
c._on_finish(a["id"], qm.DONE)
check("a job finishing DONE starts the next queued one",
      started == [b["id"]],
      f"this is what should have happened on 07-30.  got {started}")

a, b = fresh(qm.QUEUED, qm.STOPPED)
qm.set_state(a["id"], qm.RUNNING)
started.clear()
c._on_finish(a["id"], qm.DONE)
check("...and does nothing when the next job is merely stopped",
      started == [],
      "the reported behaviour, reproduced: stopped is not waiting")

a, b = fresh(qm.QUEUED, qm.QUEUED)
qm.set_state(a["id"], qm.RUNNING)
started.clear()
c._on_finish(a["id"], qm.STOPPED)
check("a user Stop does NOT roll on to the next job",
      started == [],
      "Stop means stop, not move on")

a, b = fresh(qm.QUEUED, qm.QUEUED)
qm.set_state(a["id"], qm.RUNNING)
started.clear()
c._on_finish(a["id"], qm.FAILED)
check("a FAILED job still advances, rather than idling the GPU for hours",
      started == [b["id"]], f"got {started}")

print("\n=== 6. what it is called on screen ===")
from desktop import theme                       # noqa: E402
check("'running' is shown as 'rendering'",
      theme.state_label("running") == "rendering")
for s in ("queued", "stopped", "done"):
    check(f"'{s}' is shown as itself", theme.state_label(s) == s)
check("the stored value is untouched", qm.RUNNING == "running",
      "it is in every job file and history entry on disk; renaming it would "
      "mean migrating all of them to change one word in a table")

print("\n=== 7. taking a job back out of the queue ===")
# The reverse of enqueue, so the State cell can offer both directions. Parked,
# not removed: everything already rendered stays and the queue passes over it.
a, b = fresh(qm.QUEUED, qm.QUEUED)
qm.unqueue(a["id"])
check("a queued job can be parked again",
      qm.load_job(a["id"])["state"] == qm.STOPPED)
check("...and the queue moves on to the next one",
      qm.next_queued()["id"] == b["id"])
check("parking an already-stopped job is a no-op",
      qm.unqueue(a["id"])["state"] == qm.STOPPED)

a, = fresh(qm.RUNNING)
try:
    qm.unqueue(a["id"])
    check("a running job cannot be parked", False, "no error raised")
except ValueError:
    check("a running job cannot be parked", True, "Stop is the verb for that")

a, = fresh(qm.DONE)
try:
    qm.unqueue(a["id"])
    check("a finished job cannot be parked", False, "no error raised")
except ValueError:
    check("a finished job cannot be parked", True)

a, b = fresh(qm.QUEUED, qm.STOPPED)
qm.unqueue(a["id"]); qm.enqueue(a["id"])
check("out and back in returns it to the queue",
      qm.load_job(a["id"])["state"] == qm.QUEUED)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
