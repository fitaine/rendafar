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
import paths

from . import api, panels, theme
from .jobs_table import JobsTable, job_kind
from .poller import Poller

KIND_LABEL = {"tiled": "Tiled 120k", "animation": "Animation", "still": "Still"}


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


class CollapsibleDock(QtWidgets.QDockWidget):
    """
    A panel that can be rolled up to just its title bar.

    Collapsing is not the same as closing: a closed panel is gone until you
    fetch it back from the View menu, while a collapsed one stays exactly where
    it is, keeping its place in the layout, and is one click from returning.
    That is what you want when a panel is momentarily in the way.

    Double-click the title, or use the chevron.
    """

    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self._collapsed = False
        self._sizes = None

        # Floating is simply allowed — same as Photoshop, VS Code, JetBrains,
        # even Blender itself: drag a panel far enough and it becomes its own
        # small window; drop it back onto the main window to redock.
        #
        # Two earlier attempts tried to auto-snap a floated panel back into
        # place (first: immediately on topLevelChanged, which fired mid-drag
        # and cancelled every drag before it could be dropped anywhere; then:
        # only after the mouse button was released, which still could not
        # tell "dropped back where it started" from "dropped in a genuinely
        # new spot" and sometimes reverted a legitimate redock). Both fought
        # Qt's own well-tested drag-and-dock mechanism instead of using it, and
        # both broke real dragging in a different way. Not fighting it is the
        # fix — a stray floating panel is one drag back onto the main window,
        # same as it would be in any of the apps above.

        bar = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(6)

        self._chevron = QtWidgets.QToolButton()
        self._chevron.setArrowType(QtCore.Qt.DownArrow)
        self._chevron.setAutoRaise(True)
        self._chevron.setFixedSize(18, 18)
        self._chevron.setToolTip("Collapse this panel")
        self._chevron.clicked.connect(self.toggle_collapsed)

        self._label = QtWidgets.QLabel(title)
        self._label.setFont(theme.label_font())
        self._label.setStyleSheet(f"color:{theme.DIM}")

        close = QtWidgets.QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setFixedSize(18, 18)
        close.setToolTip("Close this panel — View brings it back")
        close.clicked.connect(self.close)

        lay.addWidget(self._chevron)
        lay.addWidget(self._label)
        lay.addStretch(1)
        lay.addWidget(close)
        bar.setObjectName("dockTitle")
        bar.mouseDoubleClickEvent = lambda e: self.toggle_collapsed()
        self.setTitleBarWidget(bar)

    MAX = 16777215          # Qt's QWIDGETSIZE_MAX

    def toggle_collapsed(self):
        w = self.widget()
        if w is None:
            return
        self._collapsed = not self._collapsed
        w.setVisible(not self._collapsed)
        self._chevron.setArrowType(
            QtCore.Qt.RightArrow if self._collapsed else QtCore.Qt.DownArrow)
        self._chevron.setToolTip(
            "Expand this panel" if self._collapsed else "Collapse this panel")

        if self._collapsed:
            self._sizes = (self.minimumHeight(), self.maximumHeight())
            # Cap the HEIGHT only, and never with setFixedHeight: that also
            # freezes the minimum, and a collapsed panel then held its column
            # at whatever width it had, which could not be dragged wider again.
            # Width is left entirely free.
            self.setMinimumHeight(0)
            self.setMaximumHeight(self.titleBarWidget().sizeHint().height() + 2)
            self.setMinimumWidth(0)
            self.setMaximumWidth(self.MAX)
        else:
            lo, hi = self._sizes or (0, self.MAX)
            self.setMinimumHeight(lo)
            self.setMaximumHeight(hi if hi else self.MAX)
            self.setMinimumWidth(0)
            self.setMaximumWidth(self.MAX)

    def is_collapsed(self):
        return self._collapsed


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rendafar")
        self.setMinimumSize(880, 560)
        self.resize(1180, 760)

        # Tiphaine's own logo — Adobe-suite lettering in Blender's colours —
        # in preference to the generated tile-grid icon.
        static = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "static")
        for name in ("icon-256.png", "rendafar.ico"):
            path = os.path.join(static, name)
            if os.path.isfile(path):
                self.setWindowIcon(QtGui.QIcon(path))
                break

        # Qt hands over a dropped file's real path — the thing a web page is
        # never allowed to see, and the reason this window exists.
        self.setAcceptDrops(True)

        self._last = {}
        self._tile_stamp = None
        self._blend_pending = set()
        self._blend_jobs = []
        self._build_menus()
        self._build()
        self._default_state = self.saveState()
        self._restore_layout()

        self.poller = Poller(2000, self)
        self.poller.status_ready.connect(self.on_status)
        self.poller.server_down.connect(self.on_server_down)
        self.poller.start()

    # ── layout memory ────────────────────────────────────────────────────

    def _settings(self):
        return QtCore.QSettings("Buccino", "Rendafar")

    def _restore_layout(self):
        s = self._settings()
        geo, state = s.value("geometry"), s.value("windowState")
        if geo:
            self.restoreGeometry(geo)
        self._had_saved_layout = bool(state)
        if state:
            self.restoreState(state)

    def showEvent(self, e):
        super().showEvent(e)
        # Only the dock sizing is conditional. Maximising is not: it happens on
        # every launch, from the entry point, because tying it to "no saved
        # layout" meant it never happened again after the first run.
        if not getattr(self, "_sized", False):
            self._sized = True
            if not self._had_saved_layout:
                QtCore.QTimer.singleShot(0, self._size_docks)

    def _save_layout(self):
        s = self._settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())

    def _size_docks(self):
        self.resizeDocks([self.docks["Jobs"], self.docks["Render"]],
                         [780, 460], QtCore.Qt.Horizontal)
        # Render/Preview/GPU are three separate panels stacked in the same
        # column (not tabbed — see _build_docks), so their share of the
        # height is set directly: the tile map is the thing actually watched,
        # Preview and GPU are glanced at.
        self.resizeDocks([self.docks["Render"], self.docks["Preview"],
                         self.docks["GPU"]],
                         [480, 260, 200], QtCore.Qt.Vertical)

    def reset_layout(self):
        """Put every panel back, including any that were closed."""
        self.restoreState(self._default_state)
        for title, dock in self.docks.items():
            dock.setVisible(title != "Log")
        QtCore.QTimer.singleShot(0, self._size_docks)

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
        self.act_new.setStatusTip("Pick a .blend, or drop one on the window")
        self.act_new.triggered.connect(self.do_new)

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
        self.act_start = m.addAction("&Start queue")
        self.act_start.setShortcut("Ctrl+R")
        self.act_start.triggered.connect(self.do_start)

        self.act_stop = m.addAction("Sto&p render")
        self.act_stop.setShortcut("Ctrl+.")
        self.act_stop.triggered.connect(self.do_stop)

        m.addSeparator()
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
        self.menu_view = m
        a = m.addAction("&Refresh now")
        a.setShortcut(QtGui.QKeySequence.Refresh)
        a.triggered.connect(lambda: self.poller.refresh_now())
        m.addSeparator()
        a = m.addAction("Open the &web interface")
        a.setStatusTip("The same thing in a browser — what the phone sees")
        a.triggered.connect(lambda: self._open(api.BASE))
        m.addSeparator()
        a = m.addAction("Reset &layout")
        a.setStatusTip("Put every panel back where it started")
        a.triggered.connect(self.reset_layout)
        m.addSeparator()
        # Panel toggles are appended here by _build_docks().

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
        # The header goes in a toolbar, and the central widget is collapsed to
        # nothing. Qt always hands the central widget whatever space the docks
        # do not take, so anything left there becomes an unmovable block in the
        # middle of the window — with it gone, the panels own the whole body
        # and can be arranged freely.
        header = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(header)
        hl.setContentsMargins(14, 8, 14, 10)
        hl.setSpacing(8)
        hl.addLayout(self._build_header())
        hl.addLayout(self._build_status())

        bar = QtWidgets.QToolBar("Header")
        bar.setObjectName("headerbar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.addWidget(header)
        self.addToolBar(QtCore.Qt.TopToolBarArea, bar)

        # Every panel is a dock, so there is nothing for the central widget to
        # hold — but it must be HIDDEN, not merely sized to nothing. A zero-
        # width central widget still sits between the left and right dock
        # areas, and Qt puts a separator on each side of it, so the divider
        # between the Jobs column and the right-hand column came out as two
        # drag handles 8px apart (measured: 2.0x the 4px separator extent)
        # with a dead sliver between them. Hidden, the areas meet directly and
        # there is one handle.
        hollow = QtWidgets.QWidget()
        self.setCentralWidget(hollow)
        hollow.hide()

        self._build_docks()

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
        # The label is rewritten by _start_target() as the selection changes:
        # "Start queue" when running the queue from here on (advancing by
        # itself as each render finishes), "Resume render" when a stopped job
        # is selected. One button, because they are one intention.
        self.btn_start = QtWidgets.QPushButton("Start queue")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.do_start)
        self.btn_stop = QtWidgets.QPushButton("Stop render")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.clicked.connect(self.do_stop)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_stop)
        row.addLayout(btns, 0)
        return row

    # ── docks ────────────────────────────────────────────────────────────

    def _dock(self, title, widget, area, visible=True):
        d = CollapsibleDock(title.upper(), self)
        d.setObjectName("dock_" + title.lower().replace(" ", "_"))
        d.setWidget(widget)
        d.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        # Floatable is ON: standard Qt behaviour, a panel can become its own
        # window if dragged out far enough, same as Photoshop/VS Code/
        # JetBrains/Blender. Disabling it (an earlier attempt) also disabled
        # dragging a panel out of a tab group at all, which is the opposite of
        # what "rearrange panels freely" needs.
        d.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable |
                      QtWidgets.QDockWidget.DockWidgetClosable |
                      QtWidgets.QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        d.setVisible(visible)
        self.docks[title] = d
        return d

    def _build_docks(self):
        self.docks = {}
        # GroupedDragging is not optional here, despite the name suggesting it
        # only affects dragging groups. Qt's QMainWindowTabBar::mouseMoveEvent
        # gates its ENTIRE drag-a-tab-out-of-the-tab-bar code path behind this
        # flag. Without it a QTabBar can only ever reorder its own tabs, so a
        # tabbed panel becomes permanently trapped: you can drag panels in to
        # form a tab group, but nothing can ever come back out.
        self.setDockOptions(QtWidgets.QMainWindow.AnimatedDocks |
                            QtWidgets.QMainWindow.AllowNestedDocks |
                            QtWidgets.QMainWindow.AllowTabbedDocks |
                            QtWidgets.QMainWindow.GroupedDragging)

        # Queue and .blend settings in one table: they describe the same jobs,
        # and two panels meant reading one while remembering the other.
        self._dock("Jobs",    self._queue_panel(),   QtCore.Qt.LeftDockWidgetArea)
        self._dock("Render",  self._render_panel(),  QtCore.Qt.RightDockWidgetArea)
        self._dock("Preview", self._preview_panel(), QtCore.Qt.RightDockWidgetArea)
        self._dock("GPU",     self._gpu_panel(),     QtCore.Qt.RightDockWidgetArea)
        # The log is what you reach for when something looks wrong, not
        # something to keep on screen while everything is fine.
        self._dock("Log", self._log_panel(), QtCore.Qt.BottomDockWidgetArea,
                   visible=False)

        # Render, Preview and GPU stack as three separate panels in the right
        # column — not pre-tabbed. An earlier version tabbed Preview and GPU
        # together by default so the tile map got real height instead of a
        # sliver, but that meant "Reset layout" always re-glued them, which is
        # the opposite of what a reset should do when what you actually want
        # is every panel separate. The tile map gets its height honestly now,
        # from _size_docks below, instead of by hiding a panel behind a tab.
        #
        # Tabs still work great — dragging one panel onto another still
        # creates a tab group, same as before. This only changes the default.
        #
        # Tabs on top, like every other panelled application.
        for area in (QtCore.Qt.LeftDockWidgetArea, QtCore.Qt.RightDockWidgetArea,
                     QtCore.Qt.TopDockWidgetArea, QtCore.Qt.BottomDockWidgetArea):
            self.setTabPosition(area, QtWidgets.QTabWidget.North)

        # Sizing happens in showEvent: resizeDocks is silently ignored before
        # the window has actually been laid out.

        # Menu entries follow the panels, including when one is closed by its X.
        for title, dock in self.docks.items():
            act = dock.toggleViewAction()
            act.setText(title)
            self.menu_view.addAction(act)

    def _queue_panel(self):
        p = panels.Panel()
        top = QtWidgets.QHBoxLayout()
        self.btn_new = QtWidgets.QPushButton("+  New render")
        self.btn_new.setObjectName("primary")
        self.btn_new.clicked.connect(self.do_new)
        top.addWidget(self.btn_new)
        top.addStretch(1)
        p.lay.addLayout(top)

        self.queue = JobsTable()
        self.queue.itemSelectionChanged.connect(self._sync_buttons)
        self.queue.changed.connect(lambda: self.poller.refresh_now())
        self.queue.error.connect(
            lambda msg: self.statusBar().showMessage(msg, 8000))
        p.lay.addWidget(self.queue, 1)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(7)
        self.btn_up = QtWidgets.QPushButton("↑")
        self.btn_up.setFixedWidth(36)
        self.btn_up.clicked.connect(lambda: self.do_move(-1))
        self.btn_down = QtWidgets.QPushButton("↓")
        self.btn_down.setFixedWidth(36)
        self.btn_down.clicked.connect(lambda: self.do_move(1))
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_remove.clicked.connect(self.do_remove)
        for b in (self.btn_up, self.btn_down):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_remove)
        p.lay.addLayout(row)
        return p

    def _render_panel(self):
        p = panels.Panel()
        self.tilemap = panels.TileMap()
        p.lay.addWidget(self.tilemap, 1)
        self.render_meta = QtWidgets.QLabel("")
        self.render_meta.setObjectName("meta")
        self.render_meta.setWordWrap(True)
        p.lay.addWidget(self.render_meta)
        return p

    def _preview_panel(self):
        self.preview = panels.PreviewPanel()
        return self.preview

    def _gpu_panel(self):
        self.gpu_panel = panels.GpuPanel()
        return self.gpu_panel

    # ── .blend settings ──────────────────────────────────────────────────

    def _load_blend_info(self, blend, refresh=False):
        """
        Fetch a .blend's settings without freezing the window.

        Blender has to open the file, which is a few seconds the first time, so
        it happens on a worker and the table fills that row in when the answer
        arrives. Each file is fetched once; the server caches by (path, mtime).
        """
        if not blend or (self.queue.blend_known(blend) and not refresh):
            return
        if blend in self._blend_pending:
            return
        self._blend_pending.add(blend)

        class Job(QtCore.QThread):
            done = QtCore.Signal(str, dict)

            def run(self):
                try:
                    self.done.emit(blend, api.blend_info(blend, refresh))
                except Exception as e:
                    self.done.emit(blend, {"error": str(e)})

        job = Job(self)
        job.done.connect(self._blend_info_ready)
        job.finished.connect(job.deleteLater)
        self._blend_jobs.append(job)
        job.start()

    @QtCore.Slot(str, dict)
    def _blend_info_ready(self, blend, info):
        self._blend_pending.discard(blend)
        self.queue.set_blend_info(blend, info)
        # Redraw with what just arrived.
        if self._last.get("queue"):
            self.queue.set_jobs(self._last["queue"])
            self._sync_buttons()

    def _log_panel(self):
        self.log_panel = panels.LogPanel()
        return self.log_panel

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

        if job:
            self.job_label.setText(job["name"])
        elif s.get("next_id"):
            self.job_label.setText("Nothing running")
        else:
            # "No renders queued" is technically true of a stopped job but
            # reads as "there is nothing here", which is how a paused 40-hour
            # render came to look like an empty app with a dead Start button.
            paused = [j for j in (s.get("queue") or [])
                      if j.get("state") in self.RESUMABLE]
            self.job_label.setText(
                f"{len(paused)} render{'s' if len(paused) > 1 else ''} stopped "
                "— select one to resume" if paused else "No renders queued")

        bits = []
        p = s.get("progress")
        if p and p.get("total"):
            bits.append(f"{p['done']}/{p['total']} {p['unit']} ({p['percent']}%)")
            if p.get("eta_s") is not None:
                clock = paths.format_eta_clock(p.get("eta_at"))
                bits.append(f"ETA {hms(p['eta_s'])}"
                            + (f" ({clock})" if clock else ""))
            if p.get("avg_tile_s"):
                bits.append(f"{hms(p['avg_tile_s'])}/{p['unit'][:-1]}")
        if s.get("session_s"):
            bits.append(f"session {hms(s['session_s'])}")
        self.meta_label.setText("   ·   ".join(bits))

        self.queue.set_jobs(queue)
        self._sync_buttons()

        # Probe any .blend the table has not seen yet, one per file.
        for j in queue:
            self._load_blend_info(j.get("blend"))

        self.gpu_panel.update_gpu(s.get("gpu"))
        self._update_render_panel(s)
        self._update_preview(s)
        if self.docks["Log"].isVisible():
            self.log_panel.set_lines(s.get("log") or [])

        gpu = s.get("gpu") or {}
        if gpu.get("available"):
            self.statusBar().showMessage(
                f"{gpu.get('name','GPU')}   {round(gpu.get('power_w') or 0)} W"
                f" / {round(gpu.get('power_limit') or 0)} W   "
                f"{round(gpu.get('temp_c') or 0)} °C   "
                f"VRAM {round((gpu.get('vram_used') or 0)/1024, 1)} GB")
        else:
            self.statusBar().showMessage("GPU telemetry unavailable")

    def _update_render_panel(self, s):
        job = s.get("job")
        tiles = s.get("tiles")
        last = s.get("last_tile") or {}
        self.tilemap.set_grid(tiles, last.get("name"),
                              theme.KIND[job_kind(job)] if job else None)

        p = s.get("progress")
        if p and p.get("total"):
            unit = p["unit"]
            clock = paths.format_eta_clock(p.get("eta_at"))
            self.render_meta.setText(
                f"{p['done']} of {p['total']} {unit}   ·   {p['percent']}%   ·   "
                f"{hms(p.get('avg_tile_s'))} per {unit[:-1]}   ·   "
                f"ETA {hms(p.get('eta_s'))}"
                + (f"   ·   done {clock}" if clock else ""))
        else:
            self.render_meta.setText("")

    def _update_preview(self, s):
        if not self.docks["Preview"].isVisible() or \
                not self.preview.enabled.isChecked():
            return
        last = s.get("last_tile")
        if not last:
            self.preview.set_image(None)
            self._tile_stamp = None
            return
        # Only refetch when the file on disk actually changed.
        if last.get("mtime") == self._tile_stamp:
            return
        self._tile_stamp = last.get("mtime")
        self.preview.set_image(
            api.tile_bytes(),
            f"{last.get('name','')} · {last.get('age_s', 0)}s ago")

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

    RESUMABLE = ("stopped", "failed", "interrupted")

    def _start_target(self):
        """
        Work out what the one Start button should do right now.

        Start and Resume used to be two buttons in two different panels, and
        resuming a stopped render meant pressing both: Resume only moved the
        job back to the front of the queue as 'queued' — it never actually
        started anything — so Start stayed greyed out until you had pressed
        Resume first. For the most common action in the app ("carry on with my
        render") that is a two-panel scavenger hunt ending at a dead button.

        They are one button now. The server already supported this exactly:
        controller.start(job_id) jumps a job to the front and re-queues it if
        it was stopped, which is precisely resume-then-start in one call.

        Returns (job_id_or_None, label, enabled). Anything that presses the
        button must go through here, so the label can never promise something
        different from what the click does.
        """
        s = self._last
        if s.get("busy"):
            return None, "Start queue", False

        job = self._selected_job()
        if job:
            # An explicit selection wins over queue order: picking a row and
            # pressing Start should run THAT job, not silently something else.
            if job.get("state") in self.RESUMABLE:
                return job["id"], "Resume render", True
            if job.get("state") == "queued":
                return job["id"], "Start selected", True
            # 'done' deliberately falls through. Restarting a finished job
            # would re-render tiles that already cost hours, so it is not
            # something a single unconfirmed click should be able to do.

        if s.get("next_id"):
            return None, "Start queue", True
        return None, "Start queue", False

    def _sync_buttons(self):
        """Keep the buttons and the matching menu actions in one state."""
        s = self._last
        busy = bool(s.get("busy"))

        _, start_label, can_start = self._start_target()
        self.btn_start.setText(start_label)
        self.act_start.setText("&" + start_label)

        job = self._selected_job()
        running = bool(job and job["id"] == s.get("job_id"))
        can_edit = bool(job) and not running

        for widget, on in (
            (self.btn_start, can_start),   (self.act_start, can_start),
            (self.btn_stop, busy),         (self.act_stop, busy),
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

    def do_new(self, blend=""):
        from .new_render import NewRenderDialog
        dlg = NewRenderDialog(self, blend=blend or "")
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.poller.refresh_now()

    # ── drag and drop ────────────────────────────────────────────────────

    def dragEnterEvent(self, e):
        from .new_render import NewRenderDialog
        if NewRenderDialog.dropped_blend(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        from .new_render import NewRenderDialog
        path = NewRenderDialog.dropped_blend(e)
        if path:
            e.acceptProposedAction()
            self.do_new(path)

    def do_start(self):
        job_id, _, enabled = self._start_target()
        if not enabled:
            return
        self._act(api.start, job_id)

    def do_stop(self):
        job = self._last.get("job") or {}
        if QtWidgets.QMessageBox.question(
                self, "Stop render",
                f"Stop “{job.get('name','this render')}”?\n\n"
                "The tile being rendered right now is discarded and re-rendered "
                "when you resume. Everything already finished is kept.",
        ) == QtWidgets.QMessageBox.Yes:
            self._act(api.stop)

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
        self._save_layout()
        self.poller.stop()
        try:
            if not (self._last.get("busy")):
                api.quit_server()
        except Exception:
            pass          # closing must never fail
        e.accept()
