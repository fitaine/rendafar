"""
Telegram notifications.

Fire-and-forget: a render must never fail, stall, or block because Telegram is
down, so every send happens on a daemon thread with a short timeout, and every
failure is swallowed after one console line.  If secrets.py is missing or
empty, notifications are silently skipped and the app runs fine without them.
"""

import json
import threading
import urllib.request

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


# ── Event formatting ─────────────────────────────────────────────────────────

_ICON = {"done": "✅", "failed": "❌", "interrupted": "⚡",
         "started": "▶️", "stopped": "■", "resumed_after_boot": "🔁"}


def job_event(event, job, note=None):
    """One-line message for a queue event. `job` may be None."""
    name = job.get("name", "?") if job else "?"
    jid  = job.get("id", "") if job else ""
    icon = _ICON.get(event, "•")

    if event == "done":
        text = f"{icon} Render finished — {name}"
    elif event == "failed":
        text = f"{icon} Render FAILED — {name}"
        hist = (job or {}).get("history") or []
        if hist and hist[-1].get("note"):
            text += f"\n{hist[-1]['note']}"
    elif event == "interrupted":
        text = (f"{icon} {name} was interrupted — no live render process "
                f"found at startup (power cut or crash).")
    elif event == "resumed_after_boot":
        text = f"{icon} Auto-resumed after reboot — {name} is rendering again."
    elif event == "started":
        text = f"{icon} Started — {name}"
    elif event == "stopped":
        return          # the user pressed the button; no need to tell them
    else:
        text = f"{icon} {event} — {name}"

    if note:
        text += f"\n{note}"
    if jid:
        text += f"  ({jid})"
    send(text)
