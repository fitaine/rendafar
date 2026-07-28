"""
The panels, as dock widgets.

Photoshop-style: every panel can be dragged to another edge, torn off to
float, closed, and brought back from the View menu. The arrangement is
remembered between sessions.

Each panel is a plain QWidget here; main_window wraps them in QDockWidget. That
keeps the panels testable and reusable if the layout ever changes again.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class Panel(QtWidgets.QWidget):
    """Base: a panel with consistent padding."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(10, 10, 10, 10)
        self.lay.setSpacing(8)


class LogPanel(Panel):
    """
    Blender's output, live.

    Off by default and summoned from the View menu — it is the thing you want
    when something looks wrong, not while everything is fine.

    Follows the tail unless you scroll up, because reading a line is a good
    reason not to be yanked back to the bottom half a second later.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        row = QtWidgets.QHBoxLayout()
        self.follow = QtWidgets.QCheckBox("Follow")
        self.follow.setChecked(True)
        self.follow.setToolTip("Scroll to the newest line as it arrives")
        self.count = QtWidgets.QLabel("")
        self.count.setObjectName("meta")
        row.addWidget(self.follow)
        row.addStretch(1)
        row.addWidget(self.count)
        copy = QtWidgets.QPushButton("Copy")
        copy.setToolTip("Copy everything shown to the clipboard")
        copy.clicked.connect(self.copy_all)
        row.addWidget(copy)
        self.lay.addLayout(row)

        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(4000)     # bounded memory on long renders
        self.view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.view.setFont(theme.font(theme.MONO, 9))
        self.lay.addWidget(self.view, 1)

        self._seen = 0

    def set_lines(self, lines):
        if not lines:
            return
        # Append only what is new; re-setting the whole buffer would lose the
        # scroll position and flicker on every poll.
        if len(lines) < self._seen:
            self._seen = 0                      # a new job restarted the log
            self.view.clear()
        new = lines[self._seen:]
        if not new:
            return
        self._seen = len(lines)

        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.view.appendPlainText("\n".join(new))
        self.count.setText(f"{len(lines)} lines")
        if self.follow.isChecked() and at_end:
            bar.setValue(bar.maximum())

    def copy_all(self):
        QtWidgets.QApplication.clipboard().setText(self.view.toPlainText())


class GpuPanel(Panel):
    """Power, temperature, VRAM and utilisation, with the memory-bound warning."""

    BARS = [
        ("power", "Power",       "W"),
        ("temp",  "Temperature", "°C"),
        ("vram",  "VRAM",        "GB"),
        ("util",  "Utilisation", "%"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.name = QtWidgets.QLabel("—")
        self.name.setObjectName("meta")
        self.lay.addWidget(self.name)

        self.bars, self.values = {}, {}
        for key, label, _ in self.BARS:
            row = QtWidgets.QHBoxLayout()
            lab = QtWidgets.QLabel(label)
            lab.setStyleSheet(f"color:{theme.DIM}; font-size:11px")
            val = QtWidgets.QLabel("—")
            val.setFont(theme.font(theme.MONO, 9))
            val.setAlignment(QtCore.Qt.AlignRight)
            row.addWidget(lab)
            row.addStretch(1)
            row.addWidget(val)
            self.lay.addLayout(row)

            bar = QtWidgets.QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setRange(0, 100)
            self.lay.addWidget(bar)

            self.bars[key], self.values[key] = bar, val

        self.warning = QtWidgets.QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(
            f"color:#F3D6A6; background:rgba(255,177,59,0.09);"
            f"border:1px solid rgba(255,177,59,0.35); border-radius:7px;"
            f"padding:8px; font-size:11px")
        self.warning.hide()
        self.lay.addWidget(self.warning)
        self.lay.addStretch(1)

    def _set(self, key, value, pct, danger=False):
        self.values[key].setText(value)
        bar = self.bars[key]
        bar.setValue(int(max(0, min(100, pct or 0))))
        colour = theme.ALERT if danger else theme.GLACIER
        bar.setStyleSheet(
            f"QProgressBar {{ background:{theme.ROCK}; border:none;"
            f" border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{colour}; border-radius:3px; }}")

    def update_gpu(self, g):
        if not g or not g.get("available"):
            self.name.setText(g.get("error", "unavailable") if g else "unavailable")
            for key, _, _ in self.BARS:
                self._set(key, "—", 0)
            self.warning.hide()
            return

        self.name.setText(g.get("name", "GPU"))
        warn = g.get("warning")
        self._set("power", f"{round(g.get('power_w') or 0)} / "
                           f"{round(g.get('power_limit') or 0)} W",
                  g.get("power_pct"), danger=bool(warn))
        t = g.get("temp_c") or 0
        self._set("temp", f"{round(t)} °C", (t / 95) * 100, danger=t > 80)
        used, total = (g.get("vram_used") or 0) / 1024, (g.get("vram_total") or 1) / 1024
        self._set("vram", f"{used:.1f} / {total:.0f} GB", g.get("vram_pct"))
        self._set("util", f"{round(g.get('util_pct') or 0)} %", g.get("util_pct"))

        self.warning.setText(warn or "")
        self.warning.setVisible(bool(warn))


class TileMap(QtWidgets.QWidget):
    """
    The render's own shape: one cell per tile or frame, filling in as they
    land. Answers "how far, and are there gaps" in one glance.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cols = self.rows = 0
        self.grid = ""
        self.newest = -1
        self.kind_colour = theme.GLACIER
        # Cells are square and sized by the smaller dimension, so a short panel
        # shrinks a 30×30 grid to a postage stamp. Claim real height.
        self.setMinimumHeight(190)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)

    def set_grid(self, tiles, newest_name=None, kind_colour=None):
        if not tiles:
            self.cols = self.rows = 0
            self.grid = ""
        else:
            self.cols = tiles.get("cols") or 0
            self.rows = tiles.get("rows") or 0
            self.grid = tiles.get("grid") or ""
            self.newest = -1
            if newest_name:
                import re
                m = re.search(r"tile_r(\d+)_c(\d+)", newest_name)
                if m and self.cols:
                    self.newest = int(m.group(1)) * self.cols + int(m.group(2))
        if kind_colour:
            self.kind_colour = kind_colour
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(theme.PANEL_2))
        if not (self.cols and self.rows and self.grid):
            p.setPen(QtGui.QColor(theme.FAINT))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "no grid yet")
            return

        # Square cells, centred, using whatever space there is.
        m = 6
        w, h = self.width() - 2 * m, self.height() - 2 * m
        cell = max(1.0, min(w / self.cols, h / self.rows))
        gap = 1.0 if cell > 4 else 0.0
        ox = m + (w - cell * self.cols) / 2
        oy = m + (h - cell * self.rows) / 2

        done_c = QtGui.QColor(self.kind_colour)
        pend_c = QtGui.QColor(theme.ROCK)
        live_c = QtGui.QColor(theme.AMBER)
        p.setPen(QtCore.Qt.NoPen)
        for i, ch in enumerate(self.grid[:self.cols * self.rows]):
            r, c = divmod(i, self.cols)
            p.setBrush(live_c if i == self.newest and ch == "1"
                       else done_c if ch == "1" else pend_c)
            p.drawRect(QtCore.QRectF(ox + c * cell, oy + r * cell,
                                     cell - gap, cell - gap))


class FullscreenPreview(QtWidgets.QDialog):
    """
    A full-screen look at the current preview image.

    Opened by clicking the thumbnail in PreviewPanel. Closes on click,
    Escape, or double-click — whichever the hand reaches for first — same
    idea as the web version's lightbox, so the desktop app and the phone
    behave the same way here.
    """

    def __init__(self, pixmap, parent=None):
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("Preview")
        self.setStyleSheet(f"background:{theme.BG}")
        self.setCursor(QtCore.Qt.PointingHandCursor)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(label)

        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self._full = pixmap
        label.setPixmap(pixmap.scaled(
            screen.size() * 0.94, QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation))
        self._label = label
        self.showFullScreen()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_label"):
            self._label.setPixmap(self._full.scaled(
                self.size(), QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation))

    def mousePressEvent(self, _e):
        self.close()

    def keyPressEvent(self, e):
        if e.key() in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Space):
            self.close()
        else:
            super().keyPressEvent(e)


class PreviewPanel(Panel):
    """The most recent image the render produced, scaled to fit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout()
        self.enabled = QtWidgets.QCheckBox("Show preview")
        self.enabled.setChecked(True)
        self.enabled.setToolTip(
            "Building the preview decodes the image on this machine, which "
            "competes with the render. Turn it off on heavy scenes.")
        self.caption = QtWidgets.QLabel("")
        self.caption.setObjectName("meta")
        row.addWidget(self.enabled)
        row.addStretch(1)
        row.addWidget(self.caption)
        self.lay.addLayout(row)

        self.image = QtWidgets.QLabel("Nothing rendered yet")
        self.image.setAlignment(QtCore.Qt.AlignCenter)
        self.image.setMinimumHeight(120)
        self.image.setStyleSheet(
            f"color:{theme.FAINT}; background:{theme.PANEL_2};"
            f"border:1px solid {theme.LINE_SOFT}; border-radius:6px;")
        self.lay.addWidget(self.image, 1)
        self._pixmap = None
        self.image.mousePressEvent = self._clicked

    def _clicked(self, _event):
        if self._pixmap:
            FullscreenPreview(self._pixmap, self.window()).exec()

    def set_image(self, data, caption=""):
        self.caption.setText(caption)
        if not data:
            self._pixmap = None
            self.image.setText("Nothing rendered yet")
            self.image.setCursor(QtCore.Qt.ArrowCursor)
            return
        pm = QtGui.QPixmap()
        if not pm.loadFromData(data):
            self.image.setText("Preview unavailable")
            self._pixmap = None
            self.image.setCursor(QtCore.Qt.ArrowCursor)
            return
        self._pixmap = pm
        self.image.setCursor(QtCore.Qt.PointingHandCursor)
        self._rescale()

    def _rescale(self):
        if self._pixmap:
            self.image.setPixmap(self._pixmap.scaled(
                self.image.size(), QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()
