"""
Somewhere for errors to go when there is no console.

On 2026-11-13 the server, the window and the render all vanished at 12:15
with nothing written anywhere: under pythonw.exe, stdout and stderr go
nowhere, so the traceback that explained it was thrown away.

install("server") sends print() output, uncaught exceptions (any thread)
and hard crashes (faulthandler) to logs/server.log, and records every start
and exit, so the next one can be explained.
"""

import atexit
import faulthandler
import os
import sys
import threading
import time
import traceback

import config

MAX_BYTES = 5 * 1024 * 1024
_file = None


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def path(name):
    return os.path.join(config.LOGS_DIR, f"{name}.log")


def install(name):
    """Route this process's output and crashes to logs/<name>.log."""
    global _file
    config.ensure_dirs()
    p = path(name)
    try:
        if os.path.getsize(p) > MAX_BYTES:
            os.replace(p, p + ".old")
    except OSError:
        pass
    _file = open(p, "a", encoding="utf-8", errors="replace", buffering=1)
    _file.write(f"\n{'=' * 70}\n{_stamp()}  {name} started  (pid {os.getpid()})\n")

    # pythonw: stdout/stderr are None. A console run keeps its console AND logs.
    if sys.stdout is None or getattr(sys, "frozen", False) or sys.executable.lower().endswith("pythonw.exe"):
        sys.stdout = _file
        sys.stderr = _file

    faulthandler.enable(file=_file, all_threads=True)

    def hook(exc_type, exc, tb):
        _file.write(f"{_stamp()}  UNCAUGHT EXCEPTION (main thread)\n")
        traceback.print_exception(exc_type, exc, tb, file=_file)
        _file.flush()

    def thook(args):
        if args.exc_type is SystemExit:
            return
        _file.write(f"{_stamp()}  UNCAUGHT EXCEPTION in thread "
                    f"{args.thread.name if args.thread else '?'}\n")
        traceback.print_exception(args.exc_type, args.exc_value,
                                  args.exc_traceback, file=_file)
        _file.flush()

    sys.excepthook = hook
    threading.excepthook = thook
    atexit.register(lambda: _file.write(f"{_stamp()}  {name} exiting normally\n"))
    return _file


def note(line):
    if _file:
        _file.write(f"{_stamp()}  {line}\n")
