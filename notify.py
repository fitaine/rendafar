"""
Telegram notifications.

Fire-and-forget: a render must never fail, stall, or block because Telegram is
down, so every send happens on a daemon thread with a short timeout, and every
failure is swallowed after one console line.  If secrets.py is missing or
empty, notifications are silently skipped and the app runs fine without them.
"""

import datetime
import io
import json
import threading
import urllib.request
import uuid

# secrets_local.py, not secrets.py — a local secrets.py would shadow Python's
# stdlib `secrets` module and quietly break anything that imports it.
try:
    from secrets_local import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID = "", ""


def enabled():
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def _post(text):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text,
                         "disable_web_page_preview": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"  telegram send failed: {e}")


def send(text):
    """Send a message without ever blocking the caller."""
    if not enabled():
        return
    threading.Thread(target=_post, args=(text,), daemon=True).start()


def _post_photo(image_bytes, caption):
    """
    Telegram's sendPhoto, as hand-rolled multipart/form-data.

    No dependency taken for this (the project installs nothing beyond
    Pillow), so the multipart body is built by hand — it is only two fields.
    Falls back to a text-only message on any failure, so a broken thumbnail
    never means the notification goes missing entirely.
    """
    boundary = uuid.uuid4().hex
    parts = []

    def field(name, value):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f'{value}\r\n'.encode())

    field("chat_id", TELEGRAM_CHAT_ID)
    # Caption alone would be truncated by Telegram past 1024 chars; the
    # caller is expected to keep it well under that.
    field("caption", caption)
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
        f'filename="tile.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode())
    parts.append(image_bytes)
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        urllib.request.urlopen(req, timeout=25).read()
    except Exception as e:
        print(f"  telegram photo failed, sending text instead: {e}")
        _post(caption)


def send_photo(image_bytes, caption):
    """Send a photo with a caption, or just the caption if there is no image."""
    if not enabled():
        return
    if not image_bytes:
        send(caption)
        return
    threading.Thread(target=_post_photo, args=(image_bytes, caption),
                     daemon=True).start()


# ── Event formatting ─────────────────────────────────────────────────────────

_ICON = {"done": "✅", "failed": "❌", "interrupted": "⚡",
         "started": "▶️", "stopped": "■", "resumed_after_boot": "🔁"}


def hms(seconds):
    if not seconds or seconds < 0:
        return "—"
    s = int(seconds)
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _kind_label(job):
    """
    Tiled / animation / still, in words.

    Deliberately a small local copy rather than importing the desktop app's
    version: notify.py runs inside the server, which must stay usable with no
    Qt installed at all, and this is five lines that will not drift.
    """
    if not job:
        return "render"
    cfg = job.get("config") or {}
    if job.get("type") == "classic":
        return "still" if cfg.get("MODE") == "still" else "animation"
    return "tiled"


def _elapsed(job):
    """Wall time between last_started_at and last_ended_at, if both exist."""
    a, b = job.get("last_started_at"), job.get("last_ended_at")
    if not (a and b):
        return None
    try:
        return (datetime.datetime.fromisoformat(b)
                - datetime.datetime.fromisoformat(a)).total_seconds()
    except ValueError:
        return None


def _progress_of(job):
    """paths.progress(job), or None — imported lazily so notify.py has no
    hard dependency on the rest of the app at import time."""
    try:
        import paths
        return paths.progress(job)
    except Exception:
        return None


def _settings_line(job):
    """What was asked for, straight from the job's own config — no probing
    the .blend, so this costs nothing to show even at the "started" instant."""
    cfg = job.get("config") or {}
    kind = _kind_label(job)
    if kind == "tiled":
        le = cfg.get("LONG_EDGE")
        tl = cfg.get("TILE_LONG")
        if le:
            return f"{le:,} px long edge" + (f", {tl} px tiles" if tl else "")
    elif kind == "animation":
        a, b = cfg.get("FRAME_START"), cfg.get("FRAME_END")
        if a is None or b is None:
            # Usually absent: for classic renders the .blend is in charge, so
            # the range lives in the scene and only reaches us via the
            # manifest the render script writes.
            try:
                import paths
                rng = (paths.read_manifest(job) or {}).get("frames") or []
                if len(rng) == 2:
                    a, b = rng
            except Exception:
                pass
        if a is not None and b is not None:
            return f"frames {a}–{b}"
    elif kind == "still":
        f = cfg.get("FRAME")
        if f is not None:
            return f"frame {f}"
    return ""


def _frames_todo(job):
    """
    The frame numbers still to render, from the manifest, or None.

    The manifest keeps a `grid` of "0"/"1" per frame plus the range endpoints,
    which is more truthful than the job's config: FRAME_START/FRAME_END are
    often absent because the .blend is in charge, and the config cannot know
    what is already on disk anyway.

    Read at the instant the process is spawned, so this is still the PREVIOUS
    session's manifest — which is exactly what "what is left to do" means.
    Blender spends seconds loading the scene before rewriting it.
    """
    try:
        import paths
        m = paths.read_manifest(job)
    except Exception:
        return None
    if not m or m.get("kind") != "frames" or m.get("is_movie"):
        return None

    grid  = m.get("grid") or ""
    rng   = m.get("frames") or []
    total = m.get("total") or 0
    if not grid or len(rng) != 2 or total < 1:
        return None

    first, last = rng
    # Only the endpoints are stored, so recover the step the range was built
    # with rather than assuming every frame was requested.
    step = round((last - first) / (total - 1)) if total > 1 else 1
    step = step or 1
    return [first + i * step
            for i, c in enumerate(grid[:total]) if c != "1"]


def _resume_line(job):
    """Where a resumed render is picking up, or "" for a fresh start."""
    kind = _kind_label(job)

    if kind == "animation":
        todo = _frames_todo(job)
        if todo is None:
            return ""
        prog  = _progress_of(job) or {}
        total = prog.get("total") or 0
        done  = total - len(todo) if total else 0
        if not done:
            return ""                      # nothing rendered yet: not a resume
        bits = [f"resuming at frame {todo[0]}",
                f"{len(todo)} of {total} frames left"]
        # Frames can be missing from the middle when one errored and the run
        # carried on, so "resuming at N" alone would understate the work.
        contiguous = todo == list(range(todo[0], todo[-1] + 1,
                                        (todo[1] - todo[0]) if len(todo) > 1 else 1))
        if not contiguous:
            bits.append("with gaps earlier in the range")
        return "  ·  ".join(bits)

    prog = _progress_of(job) or {}
    total, done = prog.get("total") or 0, prog.get("done") or 0
    if total and done:
        return f"resuming  ·  {total - done} of {total} {prog['unit']} left"
    return ""


def job_event(event, job, note=None):
    """One-line message for a queue event. `job` may be None."""
    name = job.get("name", "?") if job else "?"
    jid  = job.get("id", "") if job else ""
    icon = _ICON.get(event, "•")
    kind = _kind_label(job) if job else ""
    prog = _progress_of(job) if job else None
    where = (f" — {prog['done']}/{prog['total']} {prog['unit']} "
             f"({prog['percent']}%)" if prog and prog.get("total") else "")

    if event == "done":
        text = f"{icon} Render finished — {name}"
        if prog and prog.get("total"):
            text += f"\n{prog['done']}/{prog['total']} {prog['unit']}"
        el = _elapsed(job)
        if el:
            text += f"  ·  {hms(el)}"
    elif event == "failed":
        text = f"{icon} Render FAILED — {name}{where}"
        hist = (job or {}).get("history") or []
        if hist and hist[-1].get("note"):
            text += f"\n{hist[-1]['note']}"
    elif event == "interrupted":
        text = (f"{icon} {name} was interrupted{where} — no live render "
                f"process found at startup (power cut or crash).")
    elif event == "resumed_after_boot":
        text = f"{icon} Auto-resumed after reboot — {name}{where}"
    elif event == "started":
        settings = _settings_line(job) if job else ""
        resume   = _resume_line(job) if job else ""
        text = f"{'🔁' if resume else icon} {'Resumed' if resume else 'Started'} — {name}"
        if kind or settings:
            text += f"\n{kind}{'  ·  ' + settings if settings else ''}"
        if resume:
            text += f"\n{resume}"
    elif event == "stopped":
        return          # the user pressed the button; no need to tell them
    else:
        text = f"{icon} {event} — {name}"

    if note:
        text += f"\n{note}"
    if jid:
        text += f"  ({jid})"
    send(text)
