"""
Estimating a render from what past renders of the same scene actually took.

Blender can be asked, but its answer is made from the first sample before
adaptive sampling and denoising have shown their hand — measured here, it
predicted 12.6s for a render that took 4.9s. Worse, headless Blender reports
sample progress exactly three times (0, 1, done), so there is no live counter
to build on either. Measured history is what is left, and it is better anyway.

The numbers in these checks are real: three cameras of one scene took 16.4,
21.2 and 38.4 minutes. That 2.3x spread is the reason an estimate carries its
range and its sample count instead of pretending to a single figure.

    python tests\test_history.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="history_")
config.QUEUE_DIR = TMP
config.LOGS_DIR = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(TMP, "state.json")
config.ensure_dirs()

import history                      # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def job(blend="scene.blend", scene="main", mode="still", jid="job_1"):
    return {"id": jid, "blend": f"D:\\{blend}", "type": "classic",
            "state": "queued", "config": {"SCENE": scene, "MODE": mode}}


def wipe():
    p = os.path.join(TMP, history.FILE)
    if os.path.exists(p):
        os.remove(p)


print("\n=== 1. what counts as the same kind of render ===")
check("same file, scene and mode share a signature",
      history.signature(job()) == history.signature(job(jid="other")))
check("a different scene does not",
      history.signature(job(scene="main")) != history.signature(job(scene="shadow")),
      "two scenes of one .blend can be worlds apart in cost")
check("a still and an animation frame do not",
      history.signature(job(mode="still")) != history.signature(job(mode="animation")))
check("a different .blend does not",
      history.signature(job(blend="a.blend")) != history.signature(job(blend="b.blend")))

print("\n=== 2. nothing is invented ===")
wipe()
check("no history means no estimate", history.estimate(job()) is None)
check("...and no ETA", history.job_eta_s(job()) is None,
      "a made-up number is worse than an honest blank")

print("\n=== 3. the real measurements ===")
wipe()
for secs in (984.2, 1269.8, 2304.0):        # 16.4, 21.2, 38.4 minutes
    history.record(job(), seconds_per_frame=secs)
est = history.estimate(job())
check("all three renders are remembered", est["n"] == 3, str(est))
check("the estimate is the MEDIAN, not the mean",
      est["per_frame_s"] == 1269.8,
      f"the mean would be {(984.2+1269.8+2304.0)/3:.0f}s — one slow camera "
      f"should not drag every forecast up with it.  got {est['per_frame_s']}")
check("the range is carried, not hidden",
      est["low"] == 984.2 and est["high"] == 2304.0,
      "16 to 38 minutes is a 2.3x spread; a single figure would be a fiction")

print("\n=== 4. an estimate for a job ===")
check("a still needs one frame's worth",
      history.job_eta_s(job()) == round(1269.8),
      str(history.job_eta_s(job())))
check("a job of another scene still gets nothing",
      history.job_eta_s(job(scene="shadow")) is None)

print("\n=== 5. the queue total ===")
wipe()
history.record(job(), seconds_per_frame=1200.0)
jobs = [job(jid="a"), job(jid="b"),
        job(jid="c", scene="unknown"),                    # nothing to go on
        dict(job(jid="d"), state="done")]                 # already finished
q = history.queue_eta_s(jobs)
check("only what is still to come is counted",
      q["jobs"] == 2, f"the finished one must not be added.  got {q}")
check("...and the total is their sum", q["seconds"] == 2400, str(q))
check("jobs with no history are reported, not folded in",
      q["unknown"] == 1,
      "a total that quietly omitted them would read as a promise it cannot keep")

check("a queue with nothing estimable gives None",
      history.queue_eta_s([job(jid="x", scene="nowhere")]) is None)

print("\n=== 6. it survives a bad file ===")
with open(os.path.join(TMP, history.FILE), "w", encoding="utf-8") as f:
    f.write("{ this is not json")
check("unreadable history is treated as empty, not fatal",
      history.load() == {} and history.estimate(job()) is None,
      "an estimate is a convenience and must never break a render")
history.record(job(), seconds_per_frame=600.0)
check("...and writing repairs it", history.estimate(job())["n"] == 1)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
