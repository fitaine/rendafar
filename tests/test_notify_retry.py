"""
Telegram sends survive a network that is not up yet.

After a power cut the machine boots, the scheduled task starts the server, and
the server immediately tries to say "your render was interrupted and has
resumed". Windows has not finished bringing the network up, so it failed:

    telegram photo failed, sending text instead: [Errno 11001] getaddrinfo failed
    telegram send failed:                        [Errno 11001] getaddrinfo failed

There was no retry, so the message was dropped. That is the single most useful
notification the app sends, and the one moment it was guaranteed to fail —
falling back to text did not help either, because the text send had no retry
for the same reason.

Retried now, for about eight minutes. Not indefinitely, and not for every kind
of failure: a bad token answers with an HTTP error and will answer the same way
for ever, so retrying only delays the line in the log that says so.

    python tests\test_notify_retry.py
"""

import os
import socket
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify                       # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


# No real sleeping, and no real network.
notify.time.sleep = lambda s: slept.append(s)
slept = []


class FakeResponse:
    def read(self):
        return b"{}"


def run(failures, exc):
    """Fail `failures` times with `exc`, then succeed. Returns (ok, attempts)."""
    slept.clear()
    state = {"n": 0}

    def fake_urlopen(req, timeout=None):
        state["n"] += 1
        if state["n"] <= failures:
            raise exc
        return FakeResponse()

    notify.urllib.request.urlopen = fake_urlopen
    ok = notify._send_with_retry(lambda: object(), "send")
    return ok, state["n"]


DNS = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
HTTP = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

print("\n=== 1. the power-cut case ===")
ok, n = run(failures=0, exc=DNS)
check("a working network sends first time", ok and n == 1, f"{n} attempt(s)")
check("...and does not sleep", slept == [], str(slept))

ok, n = run(failures=2, exc=DNS)
check("DNS failing twice then coming up still delivers", ok, f"{n} attempts")
check("...having waited between tries", slept == [5, 15], str(slept))

ok, n = run(failures=len(notify.RETRY_DELAYS) + 5, exc=DNS)
check("a network that never comes back gives up",
      not ok and n == len(notify.RETRY_DELAYS) + 1, f"{n} attempts")
check("...after roughly eight minutes",
      480 <= sum(notify.RETRY_DELAYS) <= 600,
      f"{sum(notify.RETRY_DELAYS)}s total — long enough for Windows to finish "
      f"starting the network, short enough not to pile up")

print("\n=== 2. what is not worth retrying ===")
ok, n = run(failures=1, exc=HTTP)
check("a bad token is not retried", not ok and n == 1,
      "Telegram answered; it will answer the same way for ever")
check("...and nothing was slept away", slept == [], str(slept))

check("an HTTP error is judged permanent", not notify._is_transient(HTTP))
check("a DNS failure is judged transient", notify._is_transient(DNS))
check("a timeout is judged transient", notify._is_transient(socket.timeout()))
check("a refused connection is judged transient",
      notify._is_transient(OSError(10061, "refused")))

print("\n=== 3. the photo path gets the same treatment ===")
src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "notify.py"), encoding="utf-8").read()
check("sendPhoto retries too", "_send_with_retry(build, \"photo\")" in src,
      "it was the photo that failed first after the power cut")
check("...and only falls back to text once the retries are spent",
      src.index("_send_with_retry(build, \"photo\")")
      < src.index("sending the caption without the photo"),
      "falling straight to text achieved nothing: the text send was failing "
      "for the same reason at the same moment")

print("\n=== 4. a send still never blocks a render ===")
check("send() hands off to a daemon thread",
      "threading.Thread" in src and "daemon=True" in src,
      "eight minutes of retries must not hold anything up")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
