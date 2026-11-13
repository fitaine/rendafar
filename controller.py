"""
Ties the queue to the runner: what starts next, and what happens after a crash.

This is the only module that decides to start a render.
"""

import threading

import config
import gpu_health
import queue_manager as qm
from runner import BadOutput, Runner, pid_alive, kill_tree


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

        If the old Blender process is STILL alive (the server crashed, Blender
        didn't), it is ADOPTED, not killed: the render carries on and the
        runner settles the job from the manifest when it ends. Killing it used
        to cost "at most one tile", but a still is one tile, and on 2026-11-11
        that threw away 4h25 of a 5 h render. While it runs the runner counts
        as busy, so no second render can start beside it.

        Returns the list of job ids that were found interrupted (adopted ones
        are not: they are still running).
        """
        config.ensure_dirs()
        found = []
        adopted = None
        with self._lock:
            st  = qm.load_state()
            pid = st.get("running_pid")
            for job in qm.all_jobs():
                if job["state"] != qm.RUNNING:
                    continue
                if (adopted is None and job["id"] == st.get("running")
                        and pid_alive(pid)):
                    self.runner.adopt(job["id"], pid)
                    adopted = job["id"]
                    continue
                qm.set_state(job["id"], qm.INTERRUPTED,
                             note="no live render process at startup")
                found.append(job["id"])
            if found and adopted is None:
                st["running"] = None
                st["running_pid"] = None
                qm.save_state(st)
        if adopted:
            self.notify("adopted", qm.load_job(adopted))

        for job_id in found:
            self.notify("interrupted", qm.load_job(job_id))
        return found

    def recover_and_resume(self):
        """
        Recovery + auto-resume. This is what runs at boot after a power cut.

        Only auto-starts when something was actually interrupted: a job that
        was queued but never started stays queued, so merely launching the
        server never fires up the GPU on its own.
        """
        had_gpu_flag = bool(qm.load_state().get("gpu_needs_reboot"))
        found = self.recover()
        # A job held back after a GPU crash is waiting for exactly this boot.
        gpu_cleared = had_gpu_flag and self.gpu_block() is None
        started = None
        if found or gpu_cleared:
            for job_id in found:
                qm.resume(job_id)
            started = self.start_next()
            if started:
                self.notify("resumed_after_boot", started)
        return found, started

    # ── control ──────────────────────────────────────────────────────────

    def gpu_block(self):
        """Why rendering is held until a reboot, or None. Clears after reboot."""
        st = qm.load_state()
        had = bool(st.get("gpu_needs_reboot"))
        reason = gpu_health.reboot_block(st)
        if had and reason is None:
            qm.save_state(st)
        return reason

    def start_next(self):
        """Start the first queued job, if the GPU is free. Returns the job or None."""
        with self._lock:
            if self.runner.is_busy():
                return None
            if self.gpu_block():
                return None
            while True:
                job = qm.next_queued()
                if job is None:
                    return None
                try:
                    started = self.runner.start(job["id"])
                    break
                except BadOutput:
                    # Already marked failed, with the reason. One bad path
                    # must not leave the GPU idle for the rest of the queue.
                    self.notify(qm.FAILED, qm.load_job(job["id"]))
        self.notify("started", started)
        return started

    def start(self, job_id):
        """Start one specific job now, jumping it to the front."""
        with self._lock:
            if self.runner.is_busy():
                raise RuntimeError("a render is already running")
            block = self.gpu_block()
            if block:
                raise RuntimeError(f"{block}. Reboot the PC first: the driver is "
                                   "unreliable until then (a relaunch hung for 2 h "
                                   "on 2026-11-12). Queued jobs resume after reboot.")
            qm.reorder([job_id])
            job = qm.load_job(job_id)
            if job and job["state"] in qm.RESUMABLE + (qm.DONE,):
                qm.set_state(job_id, qm.QUEUED, note="restarted by user")
            started = self.runner.start(job_id)
        self.notify("started", started)
        return started

    def stop(self, after_current=False):
        return self.runner.stop(after_current=after_current)

    def cancel_stop_after(self):
        return self.runner.cancel_stop_after()

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
        st["gpu_block"]  = self.gpu_block()
        return st

    # ── queue advance ────────────────────────────────────────────────────

    def _on_finish(self, job_id, state):
        job = qm.load_job(job_id)

        # Remember how long it took, whatever the outcome: a job stopped part
        # way still rendered frames, and those timings are as good as any.
        try:
            import history
            history.record(job)
        except Exception as e:
            print(f"  render history not updated: {e}")

        self.notify(state, job)

        if not config.AUTO_START_NEXT:
            return
        # A user Stop means "stop", not "move on to the next one".  A failure,
        # though, should not leave the GPU idle for hours — advance and notify.
        if state == qm.STOPPED:
            return
        self.start_next()
