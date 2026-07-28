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
        if a is not None and b is not None:
            return f"frames {a}–{b}"
    elif kind == "still":
        f = cfg.get("FRAME")
        if f is not None:
            return f"frame {f}"
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
        text = f"{icon} Started — {name}"
        if kind or settings:
            text += f"\n{kind}{'  ·  ' + settings if settings else ''}"
    elif event == "stopped":
        return          # the user pressed the button; no need to tell them
    else:
        text = f"{icon} {event} — {name}"

    if note:
        text += f"\n{note}"
    if jid:
        text += f"  ({jid})"
    send(text)
