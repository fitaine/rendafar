"""
The Jobs table: the queue and the .blend's settings in one place.

Columns:  ▍ · State · Name · Camera · Resolution · Samples · Frames · Format · Output

They can be dragged into whatever order suits. The .blend columns fill in per
row as each file is probed, which is cached, so it costs a few seconds once per
file rather than on every refresh.

Editing is limited by what the render scripts actually accept — not by taste.
`render_classic.py` has overrides for camera, frame range, output folder and
device, and deliberately none for resolution, samples or format, because the
.blend is in charge of those. A cell that cannot be changed says why when you
click it, rather than quietly doing nothing.
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

from . import api, theme

# key, heading, width hint
COLUMNS = [
    ("kind",       "",            8),
    ("state",      "State",       70),
    ("name",       "Name",        170),
    ("camera",     "Camera",      118),
    ("resolution", "Resolution",  92),
    ("samples",    "Samples",     62),
    ("frames",     "Frames",      74),
    ("format",     "Format",      62),
    ("output",     "Output",      130),   # stretches to fill what is left
]
COL = {key: i for i, (key, _, _) in enumerate(COLUMNS)}

ROLE_JOB = QtCore.Qt.UserRole + 1
ROLE_EDIT = QtCore.Qt.UserRole + 2      # (job_id, config key, kind, choices)


def job_kind(job):
    if not job:
        return "tiled"
    if job.get("type") == "classic":
        return "still" if (job.get("config") or {}).get("MODE") == "still" \
            else "animation"
    return "tiled"


class KindStripe(QtWidgets.QStyledItemDelegate):
    """The render-kind colour bar. A delegate because cell padding is wider
    than the column, which collapses an ordinary item background to nothing."""

    def paint(self, painter, option, index):
        colour = index.data(QtCore.Qt.UserRole)
        if not colour:
            return
        r = option.rect
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(colour))
        painter.drawRoundedRect(
            QtCore.QRectF(r.left() + 1, r.top() + 5, 4, max(6, r.height() - 10)),
            2, 2)
        painter.restore()


class FolderEdit(QtWidgets.QWidget):
    """A path field with a folder picker beside it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.line = QtWidgets.QLineEdit(self)
        self.line.setFrame(False)
        btn = QtWidgets.QToolButton(self)
        btn.setText("…")
        btn.setToolTip("Choose a folder")
        btn.clicked.connect(self._pick)
        lay.addWidget(self.line, 1)
        lay.addWidget(btn)
        self.setFocusProxy(self.line)

    def _pick(self):
        start = self.line.text().strip() or os.path.expanduser("~")
        if not os.path.isdir(start):
            start = os.path.dirname(start) or os.path.expanduser("~")
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Where should this render write?", start)
        if d:
            self.line.setText(os.path.normpath(d))

    def text(self):
        return self.line.text()

    def setText(self, t):
        self.line.setText(t)


class CellEditor(QtWidgets.QStyledItemDelegate):
    """
    Editors that match the setting: a dropdown for a choice, a folder picker
    for a path, a line for anything else.

    The camera dropdown is the one that matters most — the example scenes have
    six cameras between them, and which is active decides what the render
    actually shows.
    """

    edited = QtCore.Signal(str, str, object, str)   # job_id, key, value, type

    def createEditor(self, parent, option, index):
        spec = index.data(ROLE_EDIT)
        if not spec:
            return None
        _job_id, _key, kind, choices, _type = spec
        if kind == "choice":
            box = QtWidgets.QComboBox(parent)
            box.addItems(choices)
            box.setEditable(False)
            return box
        if kind == "folder":
            return FolderEdit(parent)
        return QtWidgets.QLineEdit(parent)

    def setEditorData(self, editor, index):
        value = index.data(QtCore.Qt.DisplayRole) or ""
        if isinstance(editor, QtWidgets.QComboBox):
            i = editor.findText(value)
            editor.setCurrentIndex(max(0, i))
        else:
            editor.setText("" if value in ("—", "…") else value)

    def setModelData(self, editor, model, index):
        spec = index.data(ROLE_EDIT)
        if not spec:
            return
        job_id, key, _kind, _choices, job_type = spec
        value = editor.currentText() if isinstance(editor, QtWidgets.QComboBox) \
            else editor.text().strip()
        self.edited.emit(job_id, key, value, job_type)


class JobsTable(QtWidgets.QTableWidget):

    changed = QtCore.Signal()            # something was edited; refresh
    error = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setAlternatingRowColors(False)
        self.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked |
                             QtWidgets.QAbstractItemView.SelectedClicked)

        h = self.horizontalHeader()
        h.setSectionsMovable(True)          # drag columns into any order
        h.setStretchLastSection(True)
        h.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        h.setSectionResizeMode(COL["kind"], QtWidgets.QHeaderView.Fixed)
        for key, _, width in COLUMNS:
            self.setColumnWidth(COL[key], width)

        self.setItemDelegateForColumn(COL["kind"], KindStripe(self))
        self._editor = CellEditor(self)
        self._editor.edited.connect(self._apply_edit)
        for key in ("camera", "frames", "format", "output", "name"):
            self.setItemDelegateForColumn(COL[key], self._editor)

        self.cellClicked.connect(self._explain_locked)
        self._ids = []
        self._blend = {}                    # blend path -> info dict

    # ── data in ──────────────────────────────────────────────────────────

    def set_blend_info(self, blend, info):
        self._blend[blend] = info

    def blend_known(self, blend):
        return blend in self._blend

    def selected_job_id(self):
        r = self.currentRow()
        return self._ids[r] if 0 <= r < len(self._ids) else None

    def set_jobs(self, jobs):
        keep = self.selected_job_id()
        self._ids = [j["id"] for j in jobs]
        self.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            self._fill_row(row, job)
        if keep in self._ids:
            self.selectRow(self._ids.index(keep))

    def _cell(self, row, key, text, *, editable=False, job=None,
              cfg_key=None, kind="text", choices=(), locked_why="",
              colour=None, mono=False, tip=""):
        item = QtWidgets.QTableWidgetItem(text)
        if colour:
            item.setForeground(QtGui.QColor(colour))
        if mono:
            item.setFont(theme.font(theme.MONO, 9))
        flags = QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled
        if editable:
            flags |= QtCore.Qt.ItemIsEditable
            item.setData(ROLE_EDIT, (job["id"], cfg_key, kind, list(choices),
                                     job.get("type", "tiled")))
            item.setToolTip(tip or "Double-click to change")
        elif locked_why:
            item.setToolTip(locked_why)
            item.setForeground(QtGui.QColor(theme.FAINT))
        item.setFlags(flags)
        self.setItem(row, COL[key], item)
        return item

    def _fill_row(self, row, job):
        kind = job_kind(job)
        cfg = job.get("config") or {}
        info = self._blend.get(job.get("blend")) or {}
        unknown = not info or bool(info.get("error"))

        # Editing is only safe before anything has been rendered.
        prog = job.get("progress") or {}
        started = bool(prog.get("done"))
        can_edit = job.get("state") == "queued" and not started
        why = ("Only a queued job can be changed" if job.get("state") != "queued"
               else f"{prog.get('done')} {prog.get('unit')} already rendered — "
                    f"create a new job instead" if started else "")

        stripe = QtWidgets.QTableWidgetItem("")
        stripe.setData(QtCore.Qt.UserRole, theme.KIND[kind])
        stripe.setFlags(QtCore.Qt.ItemIsEnabled)
        self.setItem(row, COL["kind"], stripe)

        self._cell(row, "state", job.get("state", "?"), mono=True,
                   colour=theme.STATE_COLOUR.get(job.get("state"), theme.DIM))
        self._cell(row, "name", job.get("name", ""))

        classic = job.get("type") == "classic"
        cams = info.get("cameras") or []
        cam = cfg.get("CAMERA") or info.get("camera") or ("…" if unknown else "—")
        self._cell(row, "camera", cam, mono=True,
                   editable=can_edit and classic and bool(cams),
                   job=job, cfg_key="CAMERA", kind="choice", choices=cams,
                   locked_why=why or ("Reading the .blend…" if unknown else
                                      "The tiled script always uses the scene camera"))

        if unknown:
            res = samples = fmt = "…"
        else:
            res = f"{info.get('final_x')}×{info.get('final_y')}"
            samples = str(info.get("samples") or "—")
            fmt = cfg.get("FILE_FORMAT") or info.get("file_format") or "—"
        self._cell(row, "resolution", res, mono=True,
                   locked_why="Comes from the .blend"
                              if classic else "Set by the tiled grid")
        self._cell(row, "samples", samples, mono=True,
                   locked_why="Comes from the .blend — never overridden")

        self._cell(row, "frames", self._frames_text(job, info, unknown), mono=True,
                   editable=can_edit and classic, job=job,
                   cfg_key="_FRAMES", kind="text",
                   locked_why=why or "A tiled render is one frame")

        self._cell(row, "format", fmt, mono=True,
                   editable=can_edit and not classic, job=job,
                   cfg_key="FILE_FORMAT", kind="choice",
                   choices=["WEBP", "JPEG", "PNG"],
                   locked_why=why or ("Comes from the .blend" if classic else ""))

        # The cell shows the folder the render actually writes to, and editing
        # it sets that same folder. For a tiled job that means splitting the
        # path back into OUTPUT_DIR + RENDER_NAME, because the script joins
        # them — writing the whole path into RENDER_NAME would nest it inside
        # itself and produce a path that goes nowhere.
        out = (job.get("output_dir") or cfg.get("OUTPUT_DIR")
               or info.get("filepath") or ("…" if unknown else "—"))
        self._cell(row, "output", out, mono=True,
                   editable=can_edit, job=job, cfg_key="_OUTPUT", kind="folder",
                   locked_why=why,
                   tip="Where this render writes — double-click, or use … to "
                       "pick a folder")

    @staticmethod
    def _frames_text(job, info, unknown):
        cfg = job.get("config") or {}
        if job.get("type") != "classic":
            return "—"
        if cfg.get("MODE") == "still":
            return str(cfg.get("FRAME") or info.get("frame_current") or "…")
        a = cfg.get("FRAME_START") or info.get("frame_start")
        b = cfg.get("FRAME_END") or info.get("frame_end")
        return f"{a}-{b}" if a and b else ("…" if unknown else "—")

    # ── editing ──────────────────────────────────────────────────────────

    def _apply_edit(self, job_id, key, value, job_type="tiled"):
        if key == "_FRAMES":
            changes = self._parse_frames(value)
            if changes is None:
                self.error.emit("Frames should look like 1-300, or a single "
                                "number for a still.")
                return
        elif key == "_OUTPUT":
            # Sent as-is; the server splits it into OUTPUT_DIR / RENDER_NAME
            # according to the job type (see paths.split_output).
            changes = {"_OUTPUT": value}
        else:
            changes = {key: value}
        try:
            d = api.edit_job(job_id, changes)
        except api.ServerDown as e:
            return self.error.emit(str(e))
        if d.get("error"):
            return self.error.emit(d["error"])
        self.changed.emit()

    @staticmethod
    def _parse_frames(text):
        text = (text or "").strip()
        if not text:
            return {}
        if "-" in text:
            a, _, b = text.partition("-")
            try:
                return {"FRAME_START": int(a), "FRAME_END": int(b)}
            except ValueError:
                return None
        try:
            return {"FRAME": int(text)}
        except ValueError:
            return None

    def _explain_locked(self, row, col):
        """A locked cell should say why, not just refuse to open."""
        item = self.item(row, col)
        if item and not (item.flags() & QtCore.Qt.ItemIsEditable) \
                and item.toolTip():
            self.error.emit(item.toolTip())
