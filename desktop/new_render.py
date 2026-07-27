"""
The New render dialog.

The one thing a browser could never do properly: a real Windows file dialog,
and a real drag-and-drop that hands over the file's actual path. Qt gives both.

Everything not on this form — resolution, samples, output folder, file format —
comes from the .blend and is never overridden. The tiled script overrides
resolution because it must (4096 px tiles of a 120 000 px image); classic
renders touch nothing.
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

from . import api, theme

KINDS = [
    ("tiled",     "Tiled 120k", "One huge image, rendered as a grid of tiles"),
    ("animation", "Animation",  "A frame range, resumable frame by frame"),
    ("still",     "Still",      "A single frame"),
]

# Matches the stripe colours in the Jobs table, so the type chosen here and the
# row it becomes are recognisably the same thing.
KIND_COLOUR = {
    "tiled":     theme.KIND["tiled"],
    "animation": theme.KIND["animation"],
    "still":     theme.KIND["still"],
}


class NewRenderDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, blend=""):
        super().__init__(parent)
        self.setWindowTitle("New render")
        self.setMinimumWidth(560)
        self.setAcceptDrops(True)
        self._jobs = []
        self._build()
        if blend:
            self.blend_edit.setText(blend)
            self.name_edit.setFocus()
        else:
            self.blend_edit.setFocus()

    # ── layout ───────────────────────────────────────────────────────────

    def _label(self, text):
        lab = QtWidgets.QLabel(text.upper())
        lab.setFont(theme.label_font())
        lab.setStyleSheet(f"color:{theme.DIM}")
        return lab

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        # Blend file
        lay.addWidget(self._label("Blend file"))
        row = QtWidgets.QHBoxLayout()
        self.blend_edit = QtWidgets.QLineEdit()
        self.blend_edit.setPlaceholderText(r"D:\scenes\valley.blend")
        self.blend_edit.textChanged.connect(self._validate)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.blend_edit, 1)
        row.addWidget(browse)
        lay.addLayout(row)

        hint = QtWidgets.QLabel("…or drop a .blend anywhere on this window.")
        hint.setStyleSheet(f"color:{theme.FAINT}; font-size:11px")
        lay.addWidget(hint)

        # Name
        lay.addWidget(self._label("Name"))
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("taken from the file name")
        lay.addWidget(self.name_edit)

        # Kind — buttons, and nothing chosen to begin with.
        #
        # A dropdown always has something selected, so it is possible to create
        # a render without ever looking at the type and get whatever happened
        # to be first. Nothing is preselected here and the dialog will not
        # submit until one is picked, which makes it a decision.
        lay.addWidget(self._label("Type"))
        self.kind_group = QtWidgets.QButtonGroup(self)
        self.kind_group.setExclusive(True)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        for i, (key, label, tip) in enumerate(KINDS):
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setProperty("kindKey", key)
            b.setProperty("kindColour", KIND_COLOUR[key])
            self.kind_group.addButton(b, i)
            row.addWidget(b, 1)
        self.kind_group.idClicked.connect(self._kind_chosen)
        lay.addLayout(row)

        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self._page_tiled())
        self.pages.addWidget(self._page_animation())
        self.pages.addWidget(self._page_still())
        self.pages.setVisible(False)          # nothing to show until a choice
        lay.addWidget(self.pages)

        note = QtWidgets.QLabel(
            "Resolution, samples, output folder and file format come from the "
            ".blend and are never changed.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{theme.FAINT}; font-size:11px")
        # A wrapped label reports the height of one line unless told otherwise,
        # so the second line was being drawn over the buttons below it.
        note.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                           QtWidgets.QSizePolicy.MinimumExpanding)
        note.setMinimumHeight(34)
        lay.addWidget(note)

        lay.addSpacing(4)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = QtWidgets.QPushButton("Add to queue")
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.create)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)

        self._validate()

    def _form(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        f.setContentsMargins(0, 4, 0, 0)
        f.setSpacing(8)
        f.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return w, f

    @staticmethod
    def _spin(lo, hi, value=0, step=1, suffix="", special="", grouped=True):
        """
        A number field with no spin buttons.

        Once a QSpinBox is styled at all, Qt stops drawing its own arrows and
        leaves two empty stubs, which looks broken. Removing them is the
        honest option: these values get typed, and the up/down keys still step.
        """
        s = QtWidgets.QSpinBox()
        s.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setValue(value)
        if suffix:
            s.setSuffix(suffix)
        if special:
            s.setSpecialValueText(special)
        s.setGroupSeparatorShown(grouped)
        s.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        return s

    def _page_tiled(self):
        w, f = self._form()
        self.long_edge = self._spin(512, 500000, 120000, 1000, " px")
        # No thousands separator: tile sizes are powers of two, and "4,096"
        # reads like a quantity rather than a dimension.
        self.tile_long = self._spin(64, 16384, 4096, 256, " px", grouped=False)

        self.fmt = QtWidgets.QComboBox()
        self.fmt.addItems(["WEBP", "JPEG", "PNG"])

        self.render_name = QtWidgets.QLineEdit()
        self.render_name.setPlaceholderText("folder for the tiles — .blend name")

        self.preview_first = QtWidgets.QCheckBox(
            "Preview first (5×5 grid, low samples)")

        f.addRow("Long edge", self.long_edge)
        # "Tile long edge", not "Tile size": the script derives the tile's
        # short edge from the image aspect so the grid comes out square
        # (N_cols == N_rows), which its own docstring says is required for the
        # single-focal-length zoom to work on both axes. Tiles are only square
        # when the image is.
        f.addRow("Tile long edge", self.tile_long)
        f.addRow("Format", self.fmt)
        f.addRow("Render name", self.render_name)
        f.addRow("", self.preview_first)

        self.grid_note = QtWidgets.QLabel("")
        self.grid_note.setWordWrap(True)
        self.grid_note.setFont(theme.font(theme.MONO, 9))
        self.grid_note.setStyleSheet(
            f"color:{theme.GLACIER}; background:{theme.PANEL_2};"
            f"border:1px solid {theme.LINE}; border-radius:7px; padding:9px;")
        f.addRow("", self.grid_note)

        # Recomputed as the numbers change, debounced so typing 120000 does not
        # fire five requests.
        self._grid_timer = QtCore.QTimer(self)
        self._grid_timer.setSingleShot(True)
        self._grid_timer.setInterval(450)
        self._grid_timer.timeout.connect(self._refresh_grid)
        for spin in (self.long_edge, self.tile_long):
            spin.valueChanged.connect(lambda _: self._grid_timer.start())
        return w

    # ── what the render will actually be ─────────────────────────────────

    def _refresh_grid(self):
        """
        Show the real geometry before committing to a render measured in days.

        Everything here comes from the pinned script's own compute_grid(), via
        the server — never from a copy of that maths.
        """
        blend = self.blend_edit.text().strip().strip('"')
        if self.kind() != "tiled" or not os.path.isfile(blend):
            self.grid_note.setText("")
            return
        self.grid_note.setText("working it out…")

        class Job(QtCore.QThread):
            done = QtCore.Signal(dict)

            def run(self):
                try:
                    self.done.emit(api.grid_preview(
                        blend, self.long_edge, self.tile_long))
                except Exception as e:
                    self.done.emit({"error": str(e)})

        job = Job(self)
        job.long_edge = self.long_edge.value()
        job.tile_long = self.tile_long.value()
        job.done.connect(self._grid_ready)
        job.finished.connect(job.deleteLater)
        self._jobs.append(job)
        job.start()

    @QtCore.Slot(dict)
    def _grid_ready(self, g):
        if g.get("error"):
            self.grid_note.setText(g["error"])
            return
        shape = "square tiles" if g["square_tiles"] else \
                f"tiles are {g['tile_w']} × {g['tile_h']} px, not square"
        self.grid_note.setText(
            f"{g['total_w']:,} × {g['total_h']:,} px   ({g['megapixels']:,} MP)\n"
            f"{g['cols']} × {g['rows']} grid  =  {g['tiles']:,} tiles\n"
            f"{shape}".replace(",", " "))

    def _page_animation(self):
        w, f = self._form()
        self.f_start = self._spin(0, 1000000, special="from the .blend")
        self.f_end   = self._spin(0, 1000000, special="from the .blend")
        f.addRow("First frame", self.f_start)
        f.addRow("Last frame", self.f_end)
        return w

    def _page_still(self):
        w, f = self._form()
        self.frame = self._spin(0, 1000000,
                                special="the scene's current frame")
        f.addRow("Frame", self.frame)
        return w

    # ── picking a file ───────────────────────────────────────────────────

    def _kind_chosen(self, index):
        self.pages.setVisible(True)
        self.pages.setCurrentIndex(index)
        for b in self.kind_group.buttons():
            colour = b.property("kindColour")
            b.setStyleSheet(
                f"QPushButton {{ background:{colour}; color:#08131A;"
                f"border-color:{colour}; font-weight:600; }}"
                if b.isChecked() else "")
        self._validate()
        self._grid_timer.start()

    def kind(self):
        """The chosen type, or None while nothing has been picked."""
        b = self.kind_group.checkedButton()
        return b.property("kindKey") if b else None

    def browse(self):
        """A real Windows file dialog — Qt's is the native one."""
        start = self.blend_edit.text().strip()
        start = os.path.dirname(start) if start else ""
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a .blend file", start,
            "Blender scene (*.blend);;All files (*.*)")
        if path:
            self.blend_edit.setText(os.path.normpath(path))

    @staticmethod
    def dropped_blend(event):
        """The .blend path from a drag event, or None."""
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(".blend") and os.path.isfile(p):
                return os.path.normpath(p)
        return None

    def dragEnterEvent(self, e):
        if self.dropped_blend(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        p = self.dropped_blend(e)
        if p:
            self.blend_edit.setText(p)
            e.acceptProposedAction()

    # ── creating ─────────────────────────────────────────────────────────

    def _validate(self):
        p = self.blend_edit.text().strip().strip('"')
        has_file = p.lower().endswith(".blend") and os.path.isfile(p)
        # Both a file AND a type: the type is never assumed.
        self.ok.setEnabled(has_file and self.kind() is not None)
        self.ok.setToolTip("" if self.kind() else "Choose a type first")
        if p and not has_file:
            self.blend_edit.setToolTip("No .blend file at that path")
        else:
            self.blend_edit.setToolTip("")
        if has_file:
            self._grid_timer.start()      # a new file means a new grid

    def payload(self):
        kind = self.kind()
        body = {
            "blend": self.blend_edit.text().strip().strip('"'),
            "name":  self.name_edit.text().strip(),
            "kind":  kind,
        }
        if kind == "tiled":
            body.update({
                "long_edge":   self.long_edge.value(),
                "tile_long":   self.tile_long.value(),
                "format":      self.fmt.currentText(),
                "render_name": self.render_name.text().strip(),
                "preview":     self.preview_first.isChecked(),
            })
        elif kind == "animation":
            if self.f_start.value():
                body["frame_start"] = self.f_start.value()
            if self.f_end.value():
                body["frame_end"] = self.f_end.value()
        else:
            if self.frame.value():
                body["frame"] = self.frame.value()
        return body

    def create(self):
        try:
            d = api.new_job(self.payload())
        except api.ServerDown as e:
            return QtWidgets.QMessageBox.warning(
                self, "Rendafar", f"The server is not responding.\n\n{e}")

        if d.get("error"):
            return QtWidgets.QMessageBox.warning(self, "Rendafar", d["error"])

        # A warning is not a failure: the job exists. Shown before closing so
        # the shared-output-folder trap is read rather than flashed past.
        if d.get("warning"):
            QtWidgets.QMessageBox.information(
                self, "Added, with a caveat",
                f"{d['job']['name']} was added.\n\n{d['warning']}")
        self.accept()
