"""
Launches Blender and supervises it.

One render at a time, always.  Two Cycles renders on one GPU would thrash VRAM
and both would crawl, so the runner refuses to start a second.
"""

import os
import re
import subprocess
import threading
import time
from collections import deque

import config
import paths
import queue_manager as qm


# ── Process helpers ──────────────────────────────────────────────────────────

# ── Live progress inside the current frame/tile ──────────────────────────────
#
# Blender prints one of these per update, on its own line (background mode uses
# newlines, not carriage returns, so line-by-line reading catches them all):
#
#   00:01.578  render | Fra: 1 | Remaining: 00:03.74 | Mem: 242M
#                     | Rendered 0/6 Tiles, Sample 1/16
#
# "Rendered a/b Tiles" is Cycles' own tiling of one image — unrelated to our
# 30×30 render tiles. The sample counter RESETS for each Cycles tile, so real
# progress through the frame is (a + sample/samples) / b, not sample/samples.

_RE_FRAME  = re.compile(r"Fra:\s*(\d+)")
_RE_CYTILE = re.compile(r"Rendered\s+(\d+)\s*/\s*(\d+)\s+Tiles")
_RE_SAMPLE = re.compile(r"Sample\s+(\d+)\s*/\s*(\d+)")
_RE_REMAIN = re.compile(r"Remaining:\s*([\d:.]+)")

# After this long with no new render line, the reading is stale (Blender is
# saving the file, loading the scene, or between tiles) and must not be shown
# as if it were live.
LIVE_STALE_S = 45


def parse_progress_line(line):
    """Extract Cycles' live progress from one stdout line, or None."""
    if "Sample" not in line and "Rendered" not in line:
        return None
    sample = _RE_SAMPLE.search(line)
    cyt    = _RE_CYTILE.search(line)
    if not sample and not cyt:
        return None

    d = {"at": time.time()}
    f = _RE_FRAME.search(line)
    if f:
        d["frame"] = int(f.group(1))
    if sample:
        d["sample"], d["samples_total"] = int(sample.group(1)), int(sample.group(2))
    if cyt:
        d["cy_done"], d["cy_total"] = int(cyt.group(1)), int(cyt.group(2))
    rem = _RE_REMAIN.search(line)
    if rem:
        d["remaining"] = rem.group(1)

    cy_total = d.get("cy_total") or 0
    st       = d.get("samples_total") or 0
    if cy_total:
        within = (d.get("sample", 0) / st) if st else 0.0
        d["percent"] = round(100.0 * (d.get("cy_done", 0) + within) / cy_total, 1)
    elif st:
        d["percent"] = round(100.0 * d.get("sample", 0) / st, 1)
    return d


# Absolute paths, never bare names: this machine's PATH has already been seen
# with all its System32 entries stripped by an installer, which silently turns
# "taskkill" into FileNotFoundError — and with it the Stop button and crash
# detection.  Stop and recovery must not depend on the health of PATH.
_SYS32    = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
_TASKLIST = os.path.join(_SYS32, "tasklist.exe")
_TASKKILL = os.path.join(_SYS32, "taskkill.exe")

# The server has no console when launched as an app, so without this every
# helper process — and Blender itself — flashes up a console window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pid_alive(pid, image="blender.exe"):
    """
    True if `pid` is running and is the expected executable.

    The image name matters: PIDs get recycled, and after a reboot the old
    Blender PID may belong to something unrelated.  Without this check, crash
    recovery could mistake a random process for a live render.
    """
    if not pid:
        return False
    try:
        out = subprocess.run(
            [_TASKLIST, "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return image.lower() in out.lower()


def kill_tree(pid):
    """
    Kill a process and everything it spawned.

    Blender spawns children, and killing only the parent leaves them holding
    the GPU — the next job would then start against a busy device.
    """
    try:
        subprocess.run([_TASKKILL, "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=30,
                       creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


# ── Runner ───────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self, on_finish=None):
        self._lock     = threading.RLock()
        self._proc     = None
        self._job_id   = None
        self._stopping = False
        self._started  = None
        self._buffer   = deque(maxlen=config.LOG_BUFFER_LINES)
        self._logfile  = None
        self._live     = None       # progress inside the current frame/tile
        self.on_finish = on_finish      # called (job_id, final_state)

    # ── status ───────────────────────────────────────────────────────────

    def is_busy(self):
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def current_job_id(self):
        with self._lock:
            return self._job_id

    def log_tail(self, n=200):
        with self._lock:
            return list(self._buffer)[-n:]

    def status(self):
        with self._lock:
            job  = qm.load_job(self._job_id) if self._job_id else None
            busy = self.is_busy()
            live = self._live
            # Between tiles, while saving, or after the render ends there are no
            # new progress lines. Showing the last one then would look like a
            # frozen bar, so drop it once stale.
            if live and (not busy or time.time() - live["at"] > LIVE_STALE_S):
                live = None
            out = {
                "busy":       busy,
                "job_id":     self._job_id,
                "job":        job,
                "pid":        self._proc.pid if self._proc else None,
                "live":       live,
                "session_s":  round(time.time() - self._started) if self._started else None,
            }
        # Both job types write a manifest with the same done/total contract —
        # gating this on "tiled" left every classic render with no progress.
        if job:
            out["progress"] = paths.progress(job)
        return out

    # ── start ────────────────────────────────────────────────────────────

    def start(self, job_id):
        with self._lock:
            if self.is_busy():
                raise RuntimeError(
                    f"already rendering {self._job_id} — only one render at a time")

            job = qm.load_job(job_id)
            if job is None:
                raise KeyError(job_id)
            if not os.path.isfile(job["blend"]):
                raise FileNotFoundError(f"blend file missing: {job['blend']}")
            if not os.path.isfile(job["script"]):
                raise FileNotFoundError(
                    f"pinned render script missing: {job['script']}")

            config.ensure_dirs()
            job_file = qm.job_path(job_id)

            env = os.environ.copy()
            env["RENDERFARM_JOB"]  = job_file
            # Blender's own stdout is C-level buffered; without this the live
            # log arrives in chunks and the UI looks frozen for minutes.
            env["PYTHONUNBUFFERED"] = "1"

            cmd = [config.BLENDER_EXE, "--background", job["blend"],
                   "--python", job["script"]]

            log_path = os.path.join(config.LOGS_DIR, f"{job_id}.log")
            self._logfile = open(log_path, "a", encoding="utf-8", errors="replace")
            self._logfile.write(
                f"\n{'=' * 70}\n"
                f"SESSION {qm._now()}\n"
                f"  blend  : {job['blend']}\n"
                f"  script : {job['script']}\n"
                f"  job    : {job_file}\n"
                f"{'=' * 70}\n")
            self._logfile.flush()

            self._proc = subprocess.Popen(
                cmd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
                cwd=config.APP_DIR,
                # NO_WINDOW as well as the process group: Blender is a console
                # program, and its output is already captured here, so a
                # console window would only flash up and steal focus.
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW,
            )
            self._job_id   = job_id
            self._stopping = False
            self._started  = time.time()
            self._buffer.clear()
            self._live     = None

            qm.set_state(job_id, qm.RUNNING)
            st = qm.load_state()
            st["running"]     = job_id
            st["running_pid"] = self._proc.pid   # for crash detection at boot
            qm.save_state(st)

            threading.Thread(target=self._pump,  daemon=True).start()
            threading.Thread(target=self._watch, daemon=True).start()
            return job

    # ── stop ─────────────────────────────────────────────────────────────

    def stop(self):
        """
        Kill the render immediately.

        By design there is no graceful pause: the manifest is flushed after
        every tile, so the most that is ever lost is the tile in flight, and
        resume re-renders it via SKIP_EXISTING.
        """
        with self._lock:
            if not self.is_busy():
                return False
            self._stopping = True
            pid = self._proc.pid
        kill_tree(pid)
        return True

    # ── internals ────────────────────────────────────────────────────────

    def _pump(self):
        """Tee Blender's stdout to the live buffer and the job log."""
        proc = self._proc
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                live = parse_progress_line(line)
                with self._lock:
                    self._buffer.append(line)
                    if live:
                        self._live = live
                    if self._logfile:
                        self._logfile.write(line + "\n")
                        self._logfile.flush()
        except (ValueError, OSError):
            pass        # pipe closed by the kill — expected on stop

    def _watch(self):
        proc = self._proc
        rc   = proc.wait()
        # Let the pump drain the last lines before deciding the outcome.
        time.sleep(0.3)

        with self._lock:
            job_id   = self._job_id
            stopping = self._stopping
            if self._logfile:
                self._logfile.close()
                self._logfile = None

        state, note = self._outcome(job_id, rc, stopping)
        qm.set_state(job_id, state, note=note)

        st = qm.load_state()
        st["running"]     = None
        st["running_pid"] = None
        qm.save_state(st)

        with self._lock:
            self._proc    = None
            self._job_id  = None
            self._started = None

        if self.on_finish:
            try:
                self.on_finish(job_id, state)
            except Exception as e:      # never let a callback kill the watcher
                print(f"  on_finish callback failed: {e}")

    def _outcome(self, job_id, rc, stopping):
        """
        Decide the final state of a finished job.

        Exit code alone is not trusted.  Blender exits 0 even after an
        exception raised at --python import time (verified 2026-07-26), so a
        tiled job is only 'done' once manifest.json agrees every tile exists.
        """
        if stopping:
            return qm.STOPPED, "stopped by user"
        if rc != 0:
            return qm.FAILED, f"blender exited with code {rc}"

        job = qm.load_job(job_id) or {}
        if job.get("type") == "tiled" and job.get("config", {}).get("PREVIEW_MODE"):
            # Previews write into a timestamped subfolder and never fill the
            # grid, so the manifest check does not apply.
            return qm.DONE, "preview"

        # Both job types write a manifest with the same done/total contract.
        prog = paths.progress(job)
        if prog is None:
            return qm.FAILED, "exit 0 but no readable manifest"
        unit = prog.get("unit", "tiles")
        if prog["total"] and prog["done"] >= prog["total"]:
            if prog["errors"]:
                return qm.FAILED, f"{len(prog['errors'])} {unit[:-1]}(s) errored"
            return qm.DONE, None
        return qm.FAILED, (f"exit 0 but only {prog['done']}/{prog['total']} "
                           f"{unit} rendered")
