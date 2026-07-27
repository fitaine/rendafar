"""
Background polling.

Qt widgets may only be touched from the GUI thread, so the HTTP call happens on
a worker thread and the result arrives as a signal. Doing it inline would
freeze the window for the duration of every request — and the whole point of
this app is watching something that takes hours.
"""

from PySide6 import QtCore

from . import api


class Poller(QtCore.QThread):
    """Polls /api/status forever, emitting each result on the GUI thread."""

    status_ready = QtCore.Signal(dict)
    server_down  = QtCore.Signal(str)

    def __init__(self, interval_ms=2000, parent=None):
        super().__init__(parent)
        self.interval_ms = interval_ms
        self._stop = False
        self._wake = QtCore.QSemaphore(0)

    def run(self):
        while not self._stop:
            try:
                self.status_ready.emit(api.status())
            except api.ServerDown as e:
                self.server_down.emit(str(e))
            except Exception as e:                      # never kill the thread
                self.server_down.emit(f"{type(e).__name__}: {e}")
            # Interruptible wait, so quitting is immediate rather than up to
            # one interval late.
            self._wake.tryAcquire(1, self.interval_ms)

    def refresh_now(self):
        """Poll again immediately — used right after an action."""
        self._wake.release()

    def stop(self):
        self._stop = True
        self._wake.release()
        self.wait(3000)
