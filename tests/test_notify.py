"""
notify.py's richer messages, and notify_watch's first-tile / periodic logic.

Never touches the real Telegram API: notify.send / notify.send_photo are
monkeypatched to capture what would have been sent, since the boundary worth
testing is "does the right message go out at the right moment", not the HTTP
call itself.

    python tests\test_notify.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="notify_test_")
config.QUEUE_DIR  = os.path.join(TMP, "queue")
config.LOGS_DIR   = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(config.QUEUE_DIR, "state.json")
config.NOTIFY_FIRST_TILE = True
config.NOTIFY_PERIODIC_S = 999999      # long, so tests control it explicitly
config.ensure_dirs()

import notify                       # noqa: E402
import notify_watch                 # noqa: E402
import paths                        # noqa: E402
import queue_manager as qm          # noqa: E402

results = []
sent, photos = [], []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


notify.enabled = lambda: True                       # pretend Telegram is set up
notify.send = lambda text: sent.append(text)
notify.send_photo = lambda data, caption: photos.append((data, caption))
notify_watch._thumbnail_for = lambda job: b"FAKEJPEG"   # skip real Blender/Pillow

print("\n=== 1. richer lifecycle messages ===")
job = qm.create_job.__wrapped__ if False else None      # placeholder, unused

blend = os.path.join(TMP, "x.blend")
open(blend, "wb").write(b"x")

j = qm.create_job(blend, name="Ecrins", cfg={"LONG_EDGE": 120000, "TILE_LONG": 4096})
sent.clear()
notify.job_event("started", j)
check("started names the kind and the settings", sent and "tiled" in sent[-1]
      and "120,000 px" in sent[-1] and "4096" in sent[-1], sent[-1] if sent else "")

jc = qm.create_job(blend, name="Pilat anim", job_type="classic",
                   cfg={"MODE": "animation", "FRAME_START": 1, "FRAME_END": 300})
sent.clear()
notify.job_event("started", jc)
check("started shows a frame range for animations",
      sent and "1–300" in sent[-1] or "1-300" in (sent[-1] if sent else ""),
      sent[-1] if sent else "")

# "done" should carry elapsed time computed from the job's own timestamps.
jd = qm.create_job(blend, name="finished job")
jd["last_started_at"] = "2026-07-27T10:00:00+00:00"
jd["last_ended_at"]   = "2026-07-27T11:30:00+00:00"
qm.save_job(jd)
sent.clear()
notify.job_event("done", jd)
check("done reports elapsed time", sent and "1h 30m" in sent[-1], sent[-1] if sent else "")

print("\n=== 2. a Stop still sends nothing ===")
sent.clear()
notify.job_event("stopped", j)
check("stopped is silent, unchanged from before", sent == [])

print("\n=== 3. first-tile photo, sent exactly once ===")
out = os.path.join(TMP, "tiles", "Ecrins")
os.makedirs(out, exist_ok=True)
manifest = os.path.join(out, "manifest.json")


def write_manifest(cols, rows, tiles_done):
    import json
    times = {f"tile_r{i//cols:04d}_c{i%cols:04d}.webp": 1.0 for i in range(tiles_done)}
    with open(manifest, "w") as f:
        json.dump({"cols": cols, "rows": rows, "tiles_done": tiles_done,
                   "tile_times": times, "started_at": "2026-07-27T10:00:00+00:00"}, f)


j2 = qm.create_job(blend, name="Ecrins 120k",
                   cfg={"LONG_EDGE": 4000, "TILE_LONG": 1000, "RENDER_NAME": "Ecrins"})
qm.set_state(j2["id"], qm.RUNNING)
st = qm.load_state(); st["running"] = j2["id"]; qm.save_state(st)

write_manifest(4, 4, 0)
photos.clear()
notify_watch._tick()
check("no tiles yet -> nothing sent", photos == [])

write_manifest(4, 4, 1)
notify_watch._tick()
check("first tile -> exactly one photo sent", len(photos) == 1,
      str(len(photos)))
check("caption says 'First tile'", "First tile" in photos[0][1], photos[0][1])

write_manifest(4, 4, 2)
notify_watch._tick()
check("second tile -> no second first-tile photo", len(photos) == 1)

print("\n=== 4. periodic update fires on schedule, not before ===")
photos.clear()
config.NOTIFY_PERIODIC_S = 5
notify_watch._seen[j2["id"]]["last_periodic"] = time.time()
write_manifest(4, 4, 3)
notify_watch._tick()
check("too soon -> no periodic update yet", photos == [])

notify_watch._seen[j2["id"]]["last_periodic"] = time.time() - 10
notify_watch._tick()
check("interval elapsed -> periodic update sent", len(photos) == 1)
check("periodic caption says 'Still going' and includes progress",
      photos and "Still going" in photos[0][1] and "/16" in photos[0][1],
      photos[0][1] if photos else "")

print("\n=== 5. attaching to an already-in-progress job never fakes 'first tile' ===")
notify_watch._seen.clear()
photos.clear()
write_manifest(4, 4, 10)               # already well underway when first noticed
notify_watch._tick()
check("mid-render on first notice -> no first-tile photo (would be a lie)",
      not any("First tile" in p[1] for p in photos))

print("\n=== 5b. a resumed render still gets a photo, honestly labelled ===")
# Suppressing "First tile" on a resume is right. Suppressing the confirmation
# photo along with it was not: it left up to NOTIFY_PERIODIC_S of silence at
# exactly the moment you want to know the render picked up properly.
config.NOTIFY_PERIODIC_S = 7200            # far away, so nothing else can fire
notify_watch._seen.clear()
photos.clear()
qm.set_state(j2["id"], qm.RUNNING)
st = qm.load_state(); st["running"] = j2["id"]; qm.save_state(st)

write_manifest(4, 4, 10)                   # picked up with 10 already on disk
notify_watch._tick()
check("on first notice of a resume -> still nothing", photos == [])

write_manifest(4, 4, 11)                   # the first genuinely NEW tile
notify_watch._tick()
check("the first new tile after resuming -> one photo", len(photos) == 1,
      f"got {len(photos)}")
check("...labelled 'Back rendering', not 'First tile'",
      photos and "Back rendering" in photos[0][1]
      and "First tile" not in photos[0][1],
      photos[0][1] if photos else "")
check("...and says which tile it is",
      photos and "first tile since resuming" in photos[0][1],
      photos[0][1] if photos else "")

write_manifest(4, 4, 12)
notify_watch._tick()
check("later tiles -> no second resume photo", len(photos) == 1, f"got {len(photos)}")

print("\n=== 5c. an animation says frame, not tile ===")
# The user's real job is a 300-frame animation; calling its output "tiles"
# is wrong in the one message meant to reassure them it is working.
anim_out = os.path.join(TMP, "anim_out")
os.makedirs(anim_out, exist_ok=True)


def write_frames_manifest(total, done):
    import json
    with open(os.path.join(anim_out, "manifest.json"), "w") as f:
        json.dump({"kind": "frames", "unit": "frames", "is_movie": False,
                   "frames": [1, total], "total": total, "frames_done": done,
                   "frame_times": {str(i + 1): 5.0 for i in range(done)},
                   "grid": "1" * done + "0" * (total - done), "errors": []}, f)


ja = qm.create_job(blend, name="Anim", job_type="classic",
                   cfg={"MODE": "animation", "OUTPUT_DIR": anim_out})
qm.set_state(ja["id"], qm.RUNNING)
st = qm.load_state(); st["running"] = ja["id"]; qm.save_state(st)
_real_cmp = paths.classic_manifest_path
paths.classic_manifest_path = lambda job: os.path.join(anim_out, "manifest.json")

notify_watch._seen.clear()
photos.clear()
write_frames_manifest(300, 0)
notify_watch._tick()
write_frames_manifest(300, 1)
notify_watch._tick()
check("a fresh animation says 'First frame'",
      photos and "First frame" in photos[0][1], photos[0][1] if photos else "")

notify_watch._seen.clear()
photos.clear()
write_frames_manifest(300, 77)             # resumed, like job_0001 really was
notify_watch._tick()
write_frames_manifest(300, 78)
notify_watch._tick()
check("a resumed animation says 'first frame since resuming'",
      photos and "first frame since resuming" in photos[0][1],
      photos[0][1] if photos else "")

paths.classic_manifest_path = _real_cmp
qm.set_state(ja["id"], qm.STOPPED)
st = qm.load_state(); st["running"] = j2["id"]; qm.save_state(st)

print("\n=== 6. nothing running -> nothing sent, and trackers are cleared ===")
qm.set_state(j2["id"], qm.STOPPED)
st = qm.load_state(); st["running"] = None; qm.save_state(st)
photos.clear()
notify_watch._tick()
check("no running job -> silent", photos == [])
check("its tracker was dropped", j2["id"] not in notify_watch._seen)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
