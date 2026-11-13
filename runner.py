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
import gpu_health
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
# "Mem: 3288M" — and everything after it is Cycles' description of the phase.
_RE_MEM    = re.compile(r"Mem:\s*([\d.]+M)")

# After this long with no new render line, the reading is stale (Blender is
# saving the file, loading the scene, or between tiles) and must not be shown
# as if it were live.
#
# Generous, because Cycles reports in sample BATCHES rather than on a timer:
# measured, a 27s render reported every 13s and a 107s render every 27s, the
# gap growing with the length of the render. On a 90 minute still that is one
# reading every 20-40 minutes. At 45s — the old value, set when progress was
# assumed to arrive continuously — the sample count would have appeared for
# under a minute and then vanished for half an hour.
#
# Age is reported alongside, so a reading is never passed off as fresher than
# it is.
LIVE_STALE_S = 3600


def _clock_to_s(text):
    """'01:17.07' or '1:02:03' as seconds, or None."""
    try:
        parts = [float(p) for p in str(text).split(":")]
    except (TypeError, ValueError):
        return None
    if not 1 <= len(parts) <= 3:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def parse_progress_line(line):
    """
    Cycles' live progress from one stdout line, or None.

    Two kinds of line matter, and both look like this:

      … | Fra: 1 | Mem: 1M | Updating Scene BVH | Building BVH 0%
      … | Fra: 1 | Remaining: 00:12.34 | Mem: 12M | Sample 1025/2000

    The first kind is what Blender shows before a single sample is taken —
    synchronising objects, loading kernels, building the BVH. It is the answer
    to "is this working or has it silently stopped": a render sitting on
    "Loading render kernels" for twenty minutes looks very different from one
    that has reached "Sample 0/2048", and neither is visible from a sample
    count alone.
    """
    if "Fra:" not in line and "Sample" not in line and "Rendered" not in line:
        return None
    sample = _RE_SAMPLE.search(line)
    cyt    = _RE_CYTILE.search(line)

    d = {"at": time.time()}

    # Everything after the memory figure is Cycles' own description of what it
    # is doing. Taken verbatim rather than matched against a list of known
    # phases, which would silently drop anything a future Blender adds.
    mem = _RE_MEM.search(line)
    if mem:
        d["mem"] = mem.group(1)
        tail = line[mem.end():].strip(" |")
        if tail:
            d["phase"] = tail

    if not sample and not cyt:
        # A status line with nothing to say is not progress.
        return d if d.get("phase") else None
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


def cli_still_frame(job):
    """
    The frame number for a still that should render through Blender's own
    loop, or None to leave the job on the script's loop.

    None is returned generously. The frame has to be certain, because "-f"
    renders exactly what it is told: an animation, a movie container, a job
    whose script predates the CLI path, or a still whose frame is only known
    once the .blend is open all stay as they were. A missing progress bar is a
    nuisance; the wrong frame is a wasted hour.
    """
    if job.get("type") != "classic":
        return None
    cfg = job.get("config") or {}
    if cfg.get("MODE") != "still":
        return None
    # A tiled still loops over its tiles inside the script; "-f" would render
    # the whole frame a second time on top.
    if cfg.get("TILE_SIZE"):
        return None

    # The pinned script has to understand the handshake. Older snapshots would
    # ignore RENDAFAR_CLI_RENDER, run their own loop, and then Blender's "-f"
    # would render the frame a second time.
    try:
        src = open(job.get("script", ""), encoding="utf-8").read()
    except OSError:
        return None
    if "RENDAFAR_CLI_RENDER" not in src:
        return None

    frame = cfg.get("FRAME")
    if isinstance(frame, int) and frame:
        return frame

    # Not set on the job: the script would use the scene's current frame, so
    # take it from the cached probe rather than opening the .blend again.
    try:
        import blend_info
        info = blend_info.read(job["blend"])
        if info.get("error"):
            return None
        scene = cfg.get("SCENE")
        if scene:
            info = next((s for s in info.get("scenes") or []
                         if s.get("scene") == scene), None) or {}
        cur = info.get("frame_current")
        return cur if isinstance(cur, int) else None
    except Exception:
        return None


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


class AdoptedProcess:
    """
    A Blender left running by a server that crashed, wrapped so the Runner can
    supervise it like one it started: same poll() / wait() / pid.

    There is no pipe to its stdout any more, so no live progress, and its exit
    code is unknowable. wait() returns 0 and _outcome() decides from the
    manifest, which the render script writes BEFORE printing anything, so a
    render whose stdout is dead still records its image correctly.
    """
    stdout = None

    def __init__(self, pid, interval=10):
        self.pid = pid
        self._interval = interval
        self._done = False

    def poll(self):
        if not self._done and not pid_alive(self.pid):
            self._done = True
        return 0 if self._done else None

    def wait(self):
        while self.poll() is None:
            time.sleep(self._interval)
        return 0


class BadOutput(ValueError):
    """A job refused at start because Blender could not save its output."""


# ── Runner ───────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self, on_finish=None):
        self._lock     = threading.RLock()
        self._proc     = None
        self._job_id   = None
        self._stopping = False
        self._stop_after = False    # waiting for the tile in flight to land
        self._started  = None
        self._buffer   = deque(maxlen=config.LOG_BUFFER_LINES)
        self._logfile  = None
        self._live     = None       # progress inside the current frame/tile
        self._adopted  = False      # supervising a render a crashed server left
        self._gpu_fault = None      # set by _gpu_watch when it kills a hung render
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
            if live:
                live = dict(live)
                live["age_s"] = round(time.time() - live["at"])
                # Blender's own estimate, counted down by however long ago it
                # said it. Cycles reports a few times per render, so without
                # this the figure on screen would sit unchanged for half an
                # hour and read as a stall.
                rem = _clock_to_s(live.get("remaining"))
                if rem is not None:
                    live["remaining_s"] = max(0, round(rem - live["age_s"]))
            out = {
                "busy":       busy,
                "stopping_after_tile": self._stop_after,
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
            if job.get("type") == "classic":
                # Jobs queued before the check existed, or whose .blend was
                # re-saved since. Failed with the reason rather than rendered
                # for nothing; the controller moves on to the next job.
                import blend_info
                bad = blend_info.output_problem(job["blend"], job.get("config"))
                if bad:
                    qm.set_state(job_id, qm.FAILED, note=bad)
                    raise BadOutput(bad)

            config.ensure_dirs()
            job_file = qm.job_path(job_id)

            env = os.environ.copy()
            env["RENDERFARM_JOB"]  = job_file
            # Blender's own stdout is C-level buffered; without this the live
            # log arrives in chunks and the UI looks frozen for minutes.
            env["PYTHONUNBUFFERED"] = "1"

            cmd = [config.BLENDER_EXE, "--background", job["blend"],
                   "--python", job["script"]]

            # A still renders through Blender's own loop, which is the only way
            # its sample progress reaches the log — bpy.ops.render.render()
            # prints nothing at all in background mode. Arguments are processed
            # in order, so the script sets the scene up and "-f" then renders it.
            #
            # Only when the frame is genuinely known: guessing it would render
            # the wrong one, which is worse than no progress bar. An animation
            # keeps the script's own loop, because which frames it still owes
            # depends on what is on disk and is not known until the .blend is
            # open.
            frame = cli_still_frame(job)
            if frame is not None:
                # -S BEFORE --python, and it is not optional when a scene is
                # chosen. Blender's -f renders the scene that was active when
                # the FILE loaded and ignores a later window.scene switch, so
                # the script would configure one scene while -f rendered
                # another — with its own untouched output path. That is exactly
                # what happened: three stills "rendered" to C:\tmp in three
                # seconds, found an old file there, skipped it and exited 0.
                scene = (job.get("config") or {}).get("SCENE")
                if scene:
                    # After the .blend, before --python: the script must see
                    # the chosen scene as the active one.
                    cmd = cmd[:3] + ["-S", str(scene)] + cmd[3:]
                cmd += ["-f", str(frame)]
                env["RENDAFAR_CLI_RENDER"] = "1"

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
            self._stop_after = False
            self._started  = time.time()
            self._buffer.clear()
            self._live     = None

            qm.set_state(job_id, qm.RUNNING)
            st = qm.load_state()
            st["running"]     = job_id
            st["running_pid"] = self._proc.pid   # for crash detection at boot
            qm.save_state(st)

            self._gpu_fault = None
            threading.Thread(target=self._pump,  daemon=True).start()
            threading.Thread(target=self._watch, daemon=True).start()
            threading.Thread(target=self._gpu_watch, args=(self._proc,),
                             daemon=True).start()
            return job

    def adopt(self, job_id, pid):
        """
        Take over a render that outlived a crashed server, instead of killing
        it. Killing threw away 4h25 of a 5 h still on 2026-11-11.

        The render carries on untouched; this only watches for it to end and
        then settles the job from the manifest, exactly as for a render this
        server started. No live progress (its stdout pipe died with the old
        server), so the job log gets a note saying why it has gone quiet.
        """
        with self._lock:
            if self.is_busy():
                raise RuntimeError(f"already rendering {self._job_id}")
            log_path = os.path.join(config.LOGS_DIR, f"{job_id}.log")
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n{'=' * 70}\nADOPTED {qm._now()}  pid {pid}\n"
                            "  The server restarted while this render was running.\n"
                            "  The render was left alone; no live progress until it ends.\n"
                            f"{'=' * 70}\n")
            except OSError:
                pass
            self._proc     = AdoptedProcess(pid)
            self._job_id   = job_id
            self._stopping = False
            self._stop_after = False
            self._started  = time.time()
            self._buffer.clear()
            self._buffer.append("(render adopted after a server restart: no live log)")
            self._live     = None
            self._adopted  = True
            threading.Thread(target=self._watch, daemon=True).start()

    # ── stop ─────────────────────────────────────────────────────────────

    def stop(self, after_current=False):
        """
        Stop the render. Immediately, or once the tile in flight has landed.

        `after_current` costs nothing in the render script and needs no
        cooperation from it, which matters: every job is pinned to a frozen
        dated script, so anything requiring a script change would not work for
        jobs already queued against an older one. Instead this watches the
        manifest, which the script flushes right after writing each tile — when
        the count goes up, the tile is safely on disk and the process can go.

        Killing a moment later loses only the seconds spent on the NEXT tile,
        which an immediate stop would have thrown away anyway.

        Returns True if a stop was started. With after_current the kill happens
        on a background thread, so this returns while the render is still
        going.
        """
        with self._lock:
            if not self.is_busy():
                return False
            # Already waiting for a tile? Asking to wait AGAIN changes nothing
            # and must not stack a second watcher. Asking to stop NOW, though,
            # is a change of mind and has to get through — that is what the
            # button does on a second press.
            if self._stop_after and after_current:
                return True
            pid = self._proc.pid
            job_id = self._job_id
            if not after_current:
                self._stopping = True
                self._stop_after = False    # the watcher will see this and exit

        if not after_current:
            kill_tree(pid)
            return True

        with self._lock:
            self._stop_after = True
        threading.Thread(target=self._kill_after_tile, args=(job_id, pid),
                         daemon=True).start()
        return True

    def stopping_after_tile(self):
        """True while waiting for the tile in flight to finish."""
        with self._lock:
            return self._stop_after

    def cancel_stop_after(self):
        """Change your mind: carry on rendering."""
        with self._lock:
            if not self._stop_after:
                return False
            self._stop_after = False
            return True

    def _kill_after_tile(self, job_id, pid):
        """Wait for the manifest count to tick up, then kill."""
        job = qm.load_job(job_id)
        start = self._done_count(job)
        t0 = time.time()

        # A tile that is already slow should not be cut short by an impatient
        # deadline, so the budget follows the job's own measured pace. The
        # floor covers a job with no timings yet; the ceiling means a wedged
        # render still stops rather than waiting for ever.
        prog = paths.progress(job) or {}
        avg = prog.get("avg_tile_s") or 0
        budget = min(max(avg * 3, config.STOP_AFTER_MIN_S), config.STOP_AFTER_MAX_S)

        while True:
            time.sleep(2)
            with self._lock:
                if not self._stop_after:
                    return                      # cancelled
                if self._proc is None or self._proc.poll() is not None:
                    self._stop_after = False    # finished on its own
                    return
            if start is None or self._done_count(job) != start:
                break                           # the tile landed
            if time.time() - t0 > budget:
                break                           # took too long; stop anyway

        with self._lock:
            if not self._stop_after or not self.is_busy():
                self._stop_after = False
                return
            self._stopping = True
            self._stop_after = False
            pid = self._proc.pid
        kill_tree(pid)

    @staticmethod
    def _done_count(job):
        """Tiles/frames recorded in the manifest, or None if unreadable."""
        prog = paths.progress(job)
        return prog.get("done") if prog else None

    # ── internals ────────────────────────────────────────────────────────

    def _pump(self):
        """Tee Blender's stdout to the live buffer and the job log."""
        proc = self._proc
        if proc.stdout is None:
            return      # adopted render: nothing to read
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

    def _log_line(self, line):
        with self._lock:
            self._buffer.append(line)
            if self._logfile:
                self._logfile.write(line + "\n")
                self._logfile.flush()

    def _gpu_watch(self, proc):
        """Kill a render whose GPU has hung (see gpu_health)."""
        watch = gpu_health.HangWatch(self._log_line)
        job = qm.load_job(self._job_id)
        last_done, since = self._done_count(job), time.time()
        while proc.poll() is None:
            time.sleep(gpu_health.GPU_SAMPLE_S)
            with self._lock:
                if self._proc is not proc or self._stopping:
                    return
            done = self._done_count(job)
            if done != last_done:
                last_done, since = done, time.time()
            avg = (paths.progress(job) or {}).get("avg_tile_s") or 0
            reason = watch.tick(time.time() - since, avg)
            if reason:
                self._log_line(f"  !! {reason}. Killing the render; "
                               "finished tiles are kept. Reboot before rendering again.")
                with self._lock:
                    if self._proc is not proc:
                        return
                    self._gpu_fault = reason
                kill_tree(proc.pid)
                return

    def _watch(self):
        proc = self._proc
        rc   = proc.wait()
        # Let the pump drain the last lines before deciding the outcome.
        time.sleep(0.3)

        with self._lock:
            job_id   = self._job_id
            stopping = self._stopping
            gpu_fault = getattr(self, "_gpu_fault", None)
            self._gpu_fault = None
            tail = list(self._buffer)[-400:]
            if self._logfile:
                self._logfile.close()
                self._logfile = None

        state, note = self._outcome(job_id, rc, stopping)
        requeue = False
        if not stopping and state == qm.FAILED:
            crash = gpu_fault or (gpu_health.crash_in_log(tail) if rc != 0 else None)
            if crash:
                # The driver is suspect until Windows reboots: relaunching on
                # it hung for 2 h on 2026-11-12. Re-queue (tiles are kept) and
                # block the queue until a reboot clears the flag.
                reason = gpu_fault or f"GPU driver crashed ({crash})"
                st = qm.load_state()
                gpu_health.mark_needs_reboot(st, reason)
                qm.save_state(st)
                note = f"{reason}. Re-queued; reboot the PC to continue."
                requeue = True
        with self._lock:
            adopted, self._adopted = self._adopted, False
        if adopted and state == qm.FAILED:
            # Its exit code is unknown, so an unfinished manifest is not proof
            # of a failure: re-queue it so the remaining work resumes.
            qm.set_state(job_id, qm.INTERRUPTED,
                         note=f"adopted render ended unfinished ({note})")
            qm.resume(job_id)
            state = qm.INTERRUPTED
        else:
            qm.set_state(job_id, state,
                         note=(f"adopted render: {note}" if adopted and note
                               else ("adopted render finished" if adopted else note)))
            if requeue:
                qm.resume(job_id)       # waits at the front; the gate holds it

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
