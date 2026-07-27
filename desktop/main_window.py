"""
The Rendafar window.

Stage 8a: the shell — status, queue, and the controls that steer a render.
The watching parts (tile map, GPU, log) and the authoring parts (drag-drop,
New render) arrive in 8b and 8c.

Laid out as a fixed frame with panes that scroll individually, deliberately:
a window that scrolls as one long page is what made the browser version feel
like a web page rather than an application.
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

import config

from . import api, theme
from .poller import Poller

KIND_LABEL = {"tiled": "Tiled 120k", "animation": "Animation", "still": "Still"}


def job_kind(job):
    """One of tiled / animation / still. Derived in one place so the colour,
    the label and the icon can never disagree."""
    if not job:
        return "tiled"
    if job.get("type") == "classic":
        return "still" if (job.get("config") or {}).get("MODE") == "still" \
            else "animation"
    return "tiled"


def hms(seconds):
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


class Card(QtWidgets.QFrame):
    """A titled panel. Everything in the window lives in one."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        head = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(title.upper())
        self.title.setObjectName("cardTitle")
        self.title.setFont(theme.label_font())
        self.aside = QtWidgets.QLabel("")
        self.aside.setObjectName("meta")
        self.aside.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.aside)
        lay.addLayout(head)

        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body, 1)


class StatusDot(QtWidgets.QWidget):
    """A small dot that pulses while a render is running."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(11, 11)
        self._colour = QtGui.QColor(theme.FAINT)
        self._pulse = False
        self._phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_state(self, colour, pulse):
        self._colour = QtGui.QColor(colour)
        self._pulse = pulse
        if pulse and not self._timer.isActive():
            self._timer.start(60)
        elif not pulse and self._timer.isActive():
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.06) % 1.0
        self.update()

    def paintEvent(self, _):
        import math
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c = QtGui.QColor(self._colour)
        if self._pulse:
            c.setAlphaF(0.35 + 0.65 * (0.5 + 0.5 * math.cos(self._phase * 2 * math.pi)))
        p.setBrush(c)
        p.setPen(QtCore.Qt.NoPen)
        p.drawEllipse(1, 1, 9, 9)


class KindStripe(QtWidgets.QStyledItemDelegate):
    """
    Paints the render-kind colour bar in the queue's first column.

    A delegate rather than an item background: the stylesheet gives cells
    7px/8px padding, which is wider than the stripe column, so a plain
    background collapsed to nothing and the colour never appeared.
    """

    def paint(self, painter, option, index):
        colour = index.data(QtCore.Qt.UserRole)
        if not colour:
            return
        r = option.rect
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(colour))
        bar = QtCore.QRectF(r.left() + 1, r.top() + 5, 4, max(6, r.height() - 10))
        painter.drawRoundedRect(bar, 2, 2)
        painter.restore()


class QueueTable(QtWidgets.QTableWidget):
    """
    The queue. A coloured stripe per row carries the render kind, so the type
    of work is readable without parsing the name.
    """

    COLS = ["", "State", "Name", "Progress"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        self.setColumnWidth(0, 8)
        self.setItemDelegateForColumn(0, KindStripe(self))
        h.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        h.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self._ids = []

    def selected_job_id(self):
        r = self.currentRow()
        return self._ids[r] if 0 <= r < len(self._ids) else None

    def set_jobs(self, jobs):
        keep = self.selected_job_id()
        self._ids = [j["id"] for j in jobs]
        self.setRowCount(len(jobs))

        for row, job in enumerate(jobs):
            kind = job_kind(job)

            stripe = QtWidgets.QTableWidgetItem("")
            stripe.setData(QtCore.Qt.UserRole, theme.KIND[kind])
            stripe.setToolTip(KIND_LABEL[kind])
            self.setItem(row, 0, stripe)

            state = QtWidgets.QTableWidgetItem(job.get("state", "?"))
            state.setForeground(QtGui.QColor(
                theme.STATE_COLOUR.get(job.get("state"), theme.DIM)))
            state.setFont(theme.font(theme.MONO, 9))
            self.setItem(row, 1, state)

            name = QtWidgets.QTableWidgetItem(job.get("name", ""))
            name.setToolTip(job.get("output_dir") or job.get("blend", ""))
            self.setItem(row, 2, name)

            p = job.get("progress")
            txt = f"{p['done']}/{p['total']} {p['unit']}" if p and p.get("total") else ""
            prog = QtWidgets.QTableWidgetItem(txt)
            prog.setFont(theme.font(theme.MONO, 9))
            prog.setForeground(QtGui.QColor(theme.DIM))
            self.setItem(row, 3, prog)

        if keep in self._ids:
            self.selectRow(self._ids.index(keep))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rendafar")
        self.setMinimumSize(880, 560)
        self.resize(1180, 760)

        icon = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "static", "rendafar.ico")
        if os.path.isfile(icon):
            self.setWindowIcon(QtGui.QIcon(icon))

        self._last = {}
        self._build_menus()
        self._build()

        self.poller = Poller(2000, self)
        self.poller.status_ready.connect(self.on_status)
        self.poller.server_down.connect(self.on_server_down)
        self.poller.start()

    # ── menus ────────────────────────────────────────────────────────────

    def _build_menus(self):
        """
        Menu bar.

        Actions live here as well as on buttons, so keyboard shortcuts have
        somewhere to be discovered. A few entries are present but disabled
        where the feature is not built yet — with a tooltip saying so, rather
        than silently missing.
        """
        bar = self.menuBar()

        # ── File ──
        m = bar.addMenu("&File")
        self.act_new = m.addAction("&New render…")
        self.act_new.setShortcut(QtGui.QKeySequence.New)
        self.act_new.setEnabled(False)
        self.act_new.setStatusTip("Arrives in the next stage, with drag-and-drop")

        a = m.addAction("Open the &blend file's folder")
        a.triggered.connect(self.open_blend_folder)
        self.act_open_blend_dir = a

        a = m.addAction("Open the &render output folder")
        a.triggered.connect(self.open_output_folder)
        self.act_open_out_dir = a

        m.addSeparator()
        a = m.addAction("&Quit Rendafar")
        a.setShortcut(QtGui.QKeySequence.Quit)
        a.triggered.connect(self.close)

        # ── Render ──
        m = bar.addMenu("&Render")
        self.act_start = m.addAction("&Start next job")
        self.act_start.setShortcut("Ctrl+R")
        self.act_start.triggered.connect(self.do_start)

        self.act_stop = m.addAction("Sto&p render")
        self.act_stop.setShortcut("Ctrl+.")
        self.act_stop.triggered.connect(self.do_stop)

        m.addSeparator()
        self.act_resume = m.addAction("Res&ume selected")
        self.act_resume.triggered.connect(self.do_resume)
        self.act_up = m.addAction("Move up")
        self.act_up.setShortcut("Ctrl+Up")
        self.act_up.triggered.connect(lambda: self.do_move(-1))
        self.act_down = m.addAction("Move down")
        self.act_down.setShortcut("Ctrl+Down")
        self.act_down.triggered.connect(lambda: self.do_move(1))
        m.addSeparator()
        self.act_remove = m.addAction("Remove from queue")
        self.act_remove.triggered.connect(self.do_remove)

        # ── View ──
        m = bar.addMenu("&View")
        a = m.addAction("&Refresh now")
        a.setShortcut(QtGui.QKeySequence.Refresh)
        a.triggered.connect(lambda: self.poller.refresh_now())
        m.addSeparator()
        a = m.addAction("Open the &web interface")
        a.setStatusTip("The same thing in a browser — what the phone sees")
        a.triggered.connect(lambda: self._open(api.BASE))

        # ── Tools ──
        m = bar.addMenu("&Tools")
        a = m.addAction("Open the Rendafar &folder")
        a.triggered.connect(lambda: self._open(config.APP_DIR))
        a = m.addAction("Open the &logs folder")
        a.triggered.connect(lambda: self._open(config.LOGS_DIR))
        a = m.addAction("Open the &queue folder")
        a.triggered.connect(lambda: self._open(config.QUEUE_DIR))
        m.addSeparator()
        a = m.addAction("Send a &test notification")
        a.setStatusTip("Checks that Telegram is working")
        a.triggered.connect(self.test_notification)

        # ── Help ──
        m = bar.addMenu("&Help")
        a = m.addAction("Rendafar on &GitHub")
        a.triggered.connect(lambda: self._open("https://github.com/fitaine/rendafar"))
        m.addSeparator()
        a = m.addAction("&About Rendafar")
        a.triggered.connect(self.about)

    def _open(self, target):
        """Open a folder in Explorer, or a URL in the browser."""
        try:
            if str(target).startswith("http"):
                import webbrowser
                webbrowser.open(target)
            elif os.path.isdir(target):
                os.startfile(target)
            else:
                self.warn(f"Not there yet:\n{target}")
        except Exception as e:
            self.warn("Could not open it.", str(e))

    def open_output_folder(self):
        job = self._selected_job() or self._last.get("job")
        out = (job or {}).get("output_dir")
        if out:
            self._open(out)
        else:
            self.warn("That job has no output folder yet.",
                      "Classic renders only know their output path once they "
                      "have started, because it comes from the .blend.")

    def open_blend_folder(self):
        job = self._selected_job() or self._last.get("job")
        blend = (job or {}).get("blend")
        if blend:
            self._open(os.path.dirname(blend))
        else:
            self.warn("Select a job first.")

    def test_notification(self):
        try:
            import notify
        except ImportError:
            return self.warn("Notifications are not configured.")
        if not notify.enabled():
            return self.warn(
                "Telegram is not configured.",
                "Copy secrets_local.example.py to secrets_local.py and fill in "
                "the bot token and chat id.")
        notify.send("Test from the Rendafar desktop app.")
        self.statusBar().showMessage("Test notification sent", 4000)

    def about(self):
        QtWidgets.QMessageBox.about(
            self, "About Rendafar",
            "<b>Rendafar</b><br><br>"
            "Watch and steer long Blender renders — from this machine or from "
            "your phone.<br><br>"
            "Built to supervise the 40–80 hour tiled renders from "
            "Blender-huge-render, and to run ordinary stills and animations.<br><br>"
            "<span style='color:#8194A6'>The queue and the renders are owned by "
            "the Rendafar server, not by this window: closing it never "
            "interrupts a render.</span>")

    # ── layout ───────────────────────────────────────────────────────────

    def _build(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)

        root.addLayout(self._build_header())
        root.addLayout(self._build_status())

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.setStatusBar(QtWidgets.QStatusBar())
        self.statusBar().showMessage("Starting…")

    def _build_header(self):
        row = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QLabel("RENDAFAR")
        brand.setObjectName("brand")
        row.addWidget(brand)
        row.addStretch(1)
        self.host_label = QtWidgets.QLabel(api.BASE.replace("http://", ""))
        self.host_label.setObjectName("meta")
        row.addWidget(self.host_label)
        return row

    def _build_status(self):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)

        self.dot = StatusDot()
        row.addWidget(self.dot, 0, QtCore.Qt.AlignTop)

        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        self.state_label = QtWidgets.QLabel("Connecting")
        self.state_label.setFont(theme.label_font())
        self.state_label.setStyleSheet(f"color:{theme.DIM}")
        self.job_label = QtWidgets.QLabel("—")
        self.job_label.setObjectName("jobName")
        self.meta_label = QtWidgets.QLabel("")
        self.meta_label.setObjectName("meta")
        col.addWidget(self.state_label)
        col.addWidget(self.job_label)
        col.addWidget(self.meta_label)
        row.addLayout(col, 1)

        btns = QtWidgets.QVBoxLayout()
        btns.setSpacing(7)
        self.btn_start = QtWidgets.QPushButton("Start next job")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.do_start)
        self.btn_stop = QtWidgets.QPushButton("Stop render")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.clicked.connect(self.do_stop)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_stop)
        row.addLayout(btns, 0)
        return row

    def _build_left(self):
        card = Card("Current render")
        self.placeholder = QtWidgets.QLabel(
            "The tile map, progress and GPU panel arrive in the next stage.")
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(QtCore.Qt.AlignCenter)
        self.placeholder.setStyleSheet(f"color:{theme.FAINT}")
        card.body.addWidget(self.placeholder, 1)
        return card

    def _build_right(self):
        card = Card("Queue")
        self.queue = QueueTable()
        self.queue.itemSelectionChanged.connect(self._sync_buttons)
        card.body.addWidget(self.queue, 1)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(7)
        self.btn_resume = QtWidgets.QPushButton("Resume")
        self.btn_resume.clicked.connect(self.do_resume)
        self.btn_up = QtWidgets.QPushButton("↑")
        self.btn_up.setFixedWidth(38)
        self.btn_up.clicked.connect(lambda: self.do_move(-1))
        self.btn_down = QtWidgets.QPushButton("↓")
        self.btn_down.setFixedWidth(38)
        self.btn_down.clicked.connect(lambda: self.do_move(1))
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_remove.clicked.connect(self.do_remove)
        for b in (self.btn_resume, self.btn_up, self.btn_down):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_remove)
        card.body.addLayout(row)
        return card

    # ── status ───────────────────────────────────────────────────────────

    @QtCore.Slot(dict)
    def on_status(self, s):
        self._last = s
        busy = bool(s.get("busy"))
        job  = s.get("job")
        queue = s.get("queue") or []

        # Uppercased here, not by the font — see theme.label_font().
        if busy:
            self.dot.set_state(theme.AMBER, True)
            self.state_label.setText(
                f"{KIND_LABEL[job_kind(job)]}  ·  Rendering".upper())
        elif job:
            self.dot.set_state(theme.STATE_COLOUR.get(job.get("state"), theme.FAINT),
                               False)
            self.state_label.setText(job.get("state", "").upper())
        else:
            self.dot.set_state(theme.FAINT, False)
            self.state_label.setText("Ready" if s.get("next_id") else "Idle")

        self.job_label.setText(
            job["name"] if job else
            ("Nothing running" if s.get("next_id") else "No renders queued"))

        bits = []
        p = s.get("progress")
        if p and p.get("total"):
            bits.append(f"{p['done']}/{p['total']} {p['unit']} ({p['percent']}%)")
            if p.get("eta_s") is not None:
                bits.append(f"ETA {hms(p['eta_s'])}")
            if p.get("avg_tile_s"):
                bits.append(f"{hms(p['avg_tile_s'])}/{p['unit'][:-1]}")
        if s.get("session_s"):
            bits.append(f"session {hms(s['session_s'])}")
        self.meta_label.setText("   ·   ".join(bits))

        self.queue.set_jobs(queue)
        self._sync_buttons()

        gpu = s.get("gpu") or {}
        if gpu.get("available"):
            self.statusBar().showMessage(
                f"{gpu.get('name','GPU')}   {round(gpu.get('power_w') or 0)} W"
                f" / {round(gpu.get('power_limit') or 0)} W   "
                f"{round(gpu.get('temp_c') or 0)} °C   "
                f"VRAM {round((gpu.get('vram_used') or 0)/1024, 1)} GB")
        else:
            self.statusBar().showMessage("GPU telemetry unavailable")

    @QtCore.Slot(str)
    def on_server_down(self, why):
        self.dot.set_state(theme.ALERT, False)
        self.state_label.setText("No connection")
        self.job_label.setText("Rendafar server not responding")
        self.meta_label.setText(why[:120])
        self.statusBar().showMessage("Server down — retrying…")

    def _selected_job(self):
        jid = self.queue.selected_job_id()
        return next((j for j in (self._last.get("queue") or [])
                     if j["id"] == jid), None)

    def _sync_buttons(self):
        """Keep the buttons and the matching menu actions in one state."""
        s = self._last
        busy = bool(s.get("busy"))
        can_start = not busy and bool(s.get("next_id"))

        job = self._selected_job()
        running = bool(job and job["id"] == s.get("job_id"))
        can_resume = bool(job) and job.get("state") in ("stopped", "failed",
                                                        "interrupted")
        can_edit = bool(job) and not running

        for widget, on in (
            (self.btn_start, can_start),   (self.act_start, can_start),
            (self.btn_stop, busy),         (self.act_stop, busy),
            (self.btn_resume, can_resume), (self.act_resume, can_resume),
            (self.btn_up, can_edit),       (self.act_up, can_edit),
            (self.btn_down, can_edit),     (self.act_down, can_edit),
            (self.btn_remove, can_edit),   (self.act_remove, can_edit),
        ):
            widget.setEnabled(on)

        has_job = bool(job or s.get("job"))
        self.act_open_blend_dir.setEnabled(has_job)
        self.act_open_out_dir.setEnabled(has_job)

    # ── actions ──────────────────────────────────────────────────────────

    def _act(self, fn, *a):
        try:
            d = fn(*a)
        except api.ServerDown as e:
            return self.warn("Rendafar server is not responding.", str(e))
        if isinstance(d, dict) and d.get("error"):
            return self.warn(d["error"])
        self.poller.refresh_now()

    def do_start(self):
        self._act(api.start)

    def do_stop(self):
        job = self._last.get("job") or {}
        if QtWidgets.QMessageBox.question(
                self, "Stop render",
                f"Stop “{job.get('name','this render')}”?\n\n"
                "The tile being rendered right now is discarded and re-rendered "
                "when you resume. Everything already finished is kept.",
        ) == QtWidgets.QMessageBox.Yes:
            self._act(api.stop)

    def do_resume(self):
        jid = self.queue.selected_job_id()
        if jid:
            self._act(api.resume, jid)

    def do_move(self, offset):
        jid = self.queue.selected_job_id()
        if jid:
            self._act(api.move, jid, offset)

    def do_remove(self):
        jid = self.queue.selected_job_id()
        if not jid:
            return
        if QtWidgets.QMessageBox.question(
                self, "Remove from queue",
                "Remove this job from the queue?\n\n"
                "Rendered tiles stay on disk — only the job is removed.",
        ) == QtWidgets.QMessageBox.Yes:
            self._act(api.remove, jid)

    def warn(self, text, detail=""):
        m = QtWidgets.QMessageBox(self)
        m.setIcon(QtWidgets.QMessageBox.Warning)
        m.setWindowTitle("Rendafar")
        m.setText(text)
        if detail:
            m.setDetailedText(detail)
        m.exec()

    # ── closing ──────────────────────────────────────────────────────────

    def closeEvent(self, e):
        """
        Closing the window closes Rendafar — unless a render is running, in
        which case the server stays up so the render remains supervised and
        reachable from the phone. Same rule as the browser version, made
        explicit here because the X is now the only way out.
        """
        self.poller.stop()
        try:
            if not (self._last.get("busy")):
                api.quit_server()
        except Exception:
            pass          # closing must never fail
        e.accept()
