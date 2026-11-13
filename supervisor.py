"""
Keeps the server alive: restarts it if it dies.

The desktop app launches this instead of server.py. A clean exit (window
closed, idle close, Quit) ends the supervisor too; anything else is a crash
and the server comes back in a few seconds. On restart the server adopts the
render that kept going on its own (runner.py), so a crash costs nothing.

A server that keeps dying within seconds of starting (port taken, broken
config) is given up on after a few tries instead of looping for ever.
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import crashlog

RESTART_DELAY_S = 5
QUICK_DEATH_S   = 30      # died this soon after starting = not worth looping
MAX_QUICK       = 3


def main():
    crashlog.install("supervisor")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    quick = 0
    while True:
        t0 = time.time()
        proc = subprocess.Popen(
            [pyw, os.environ.get("RENDAFAR_SUPERVISED", os.path.join(HERE, "server.py"))], cwd=HERE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        crashlog.note(f"server started (pid {proc.pid})")
        rc = proc.wait()
        if rc == 0:
            crashlog.note("server closed cleanly; supervisor done")
            return
        lived = time.time() - t0
        crashlog.note(f"SERVER DIED  exit code {rc} after {lived / 60:.1f} min; "
                      f"see server.log. Restarting in {RESTART_DELAY_S} s.")
        quick = quick + 1 if lived < QUICK_DEATH_S else 0
        if quick >= MAX_QUICK:
            crashlog.note(f"died {quick} times within {QUICK_DEATH_S} s of starting; giving up")
            return
        time.sleep(RESTART_DELAY_S)


if __name__ == "__main__":
    main()
