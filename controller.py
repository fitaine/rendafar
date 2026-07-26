"""
Ties the queue to the runner: what starts next, and what happens after a crash.

This is the only module that decides to start a render.
"""

import threading

import config
import queue_manager as qm
from runner import Runner, pid_alive


class Controller:
    def __init__(self, notify=None):
        self._lock  = threading.RLock()
        self.notify = notify or (lambda event, job, note=None: None)
        self.runner = Runner(on_finish=self._on_finish)

    # ── recovery ─────────────────────────────────────────────────────────

    def recover(self):
        """
        Reconcile disk state with reality at startup.

        A job left in 'running' with no live Blender behind it did not finish —
        the machine lost power, or the app was killed.  Mark it 'interrupted'
        so it is visibly distinct from a job the user stopped on purpose.

        Returns the list of job ids that were found interrupted.
        """
        config.ensure_dirs()
        found = []
        with self._lock:
            st  = qm.load_state()
            pid = st.get("running_pid")
            for job in qm.all_jobs():
                if job["state"] != qm.RUNNING:
                    continue
                if job["id"] == st.get("running") and pid_alive(pid):
                    continue        # genuinely still rendering (app restarted alone)
                qm.set_state(job["id"], qm.INTERRUPTED,
                             note="no live render process at startup")
                found.append(job["id"])
            if found:
                st["running"] = None
                st["running_pid"] = None
                qm.save_state(st)

        for job_id in found:
            self.notify("interrupted", qm.load_job(job_id))
        return found

    def recover_and_resume(self):
        """Recovery + auto-resume. This is what runs at boot after a power cut."""
        found = self.recover()
        for job_id in found:
            qm.resume(job_id)
        started = self.start_next()
        return found, started

    # ── control ──────────────────────────────────────────────────────────

    def start_next(self):
        """Start the first queued job, if the GPU is free. Returns the job or None."""
        with self._lock:
            if self.runner.is_busy():
                return None
            job = qm.next_queued()
            if job is None:
                return None
            started = self.runner.start(job["id"])
        self.notify("started", started)
        return started

    def start(self, job_id):
        """Start one specific job now, jumping it to the front."""
        with self._lock:
            if self.runner.is_busy():
                raise RuntimeError("a render is already running")
            qm.reorder([job_id])
            job = qm.load_job(job_id)
            if job and job["state"] in qm.RESUMABLE + (qm.DONE,):
                qm.set_state(job_id, qm.QUEUED, note="restarted by user")
            started = self.runner.start(job_id)
        self.notify("started", started)
        return started

    def stop(self):
        return self.runner.stop()

    def status(self):
        st = self.runner.status()
        st["queue"]   = qm.all_jobs()
        nxt           = qm.next_queued()
        st["next_id"] = nxt["id"] if nxt else None
        # There is a short window between one job's process ending and the next
        # one starting where `busy` is False but the queue is not actually
        # finished.  Callers that mean "is there work left" must use this, or
        # they will see a phantom idle moment between every pair of jobs.
        st["queue_idle"] = (not st["busy"]) and nxt is None
        return st

    # ── queue advance ────────────────────────────────────────────────────

    def _on_finish(self, job_id, state):
        job = qm.load_job(job_id)
        self.notify(state, job)

        if not config.AUTO_START_NEXT:
            return
        # A user Stop means "stop", not "move on to the next one".  A failure,
        # though, should not leave the GPU idle for hours — advance and notify.
        if state == qm.STOPPED:
            return
        self.start_next()
