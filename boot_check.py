"""
Runs at logon. Decides whether Rendafar needs to come back on its own.

Rendafar is a normal app: you double-click the launcher when you want it, and
closing it stops it.  The ONE exception is an interrupted render — a power cut
mid-render must not need a human.  So this runs at every logon, and:

    a render was in progress  ->  start Rendafar (it resumes and notifies)
    nothing was rendering     ->  do nothing, exit silently

The signal is queue/state.json.  The server clears `running` when a job ends,
so a non-empty `running` at logon means the render never finished — power cut,
crash, or the window was closed mid-render.  In all three cases resuming is the
useful behaviour.

Deliberately silent when there is nothing to do: no window, no notification,
no GPU.  This must be invisible on an ordinary morning.
"""

import os
import socket
import subprocess
import sys

import config
import queue_manager as qm

LAUNCHER = os.path.join(config.APP_DIR, "RUN SERVER LOOP.bat")


def already_running():
    """True if something is already listening on the app's port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.7)
        return s.connect_ex(("127.0.0.1", config.PORT)) == 0


def interrupted_job():
    """The job that was mid-render when we lost the machine, or None."""
    try:
        st = qm.load_state()
    except Exception:
        return None
    job_id = st.get("running")
    if not job_id:
        return None
    job = qm.load_job(job_id)
    if job and job.get("state") == qm.RUNNING:
        return job
    return None


def main():
    job = interrupted_job()
    if job is None:
        print("nothing was rendering - Rendafar stays off")
        return 0

    if already_running():
        print(f"{job['id']} was interrupted, but Rendafar is already up - leaving it")
        return 0

    print(f"{job['id']} ({job['name']}) was interrupted - starting Rendafar")
    if not os.path.isfile(LAUNCHER):
        print(f"launcher missing: {LAUNCHER}")
        return 1

    # New console so the render is visible and supervised exactly as it would be
    # if she had launched it herself. The server's own recover_and_resume()
    # does the resuming and the Telegram notification.
    subprocess.Popen([LAUNCHER], cwd=config.APP_DIR, close_fds=True,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
