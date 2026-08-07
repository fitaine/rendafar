"""
The Jobs table: the queue and the .blend's settings in one place.

Columns:  ▍ · State · Name · Scene · Camera · Resolution · Samples · Frames ·
          Format · Output

A .blend can hold several scenes, each with its own cameras, resolution, frame
range and output path. Everything from Camera rightwards describes the SELECTED
scene, not whichever one happened to be active when the file was saved.

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
    ("scene",      "Scene",       104),
    ("camera",     "Camera",      118),
    # Wide enough for a tiled render's assembled size: "120000×120000" is 13
    # mono characters, where a classic render's "4000×4000" is 9.
    ("resolution", "Resolution",  116),
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
    """
    A path field with a folder picker beside it.

    Sized explicitly: as a bare container it collapsed to the row height and
    the field inside came out as a sliver too thin to read, let alone type in.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.line = QtWidgets.QLineEdit(self)
        self.line.setMinimumHeight(26)
        btn = QtWidgets.QToolButton(self)
        btn.setText("…")
        btn.setMinimumHeight(26)
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

    # A cell editor gets the row's height, and the stylesheet's padding on
    # QComboBox/QLineEdit then leaves almost nothing for the text — the camera
    # dropdown came out as a flat sliver. Rows and editors are sized together
    # here so an editor is always usable.
    ROW_HEIGHT = 34

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), self.ROW_HEIGHT))
        return s

    def updateEditorGeometry(self, editor, option, index):
        r = QtCore.QRect(option.rect)
        if r.height() < self.ROW_HEIGHT:
            r.setHeight(self.ROW_HEIGHT)
        editor.setGeometry(r)

    def createEditor(self, parent, option, index):
        spec = index.data(ROLE_EDIT)
        if not spec:
            return None
        _job_id, _key, kind, choices, _type = spec
        if kind == "choice":
            box = QtWidgets.QComboBox(parent)
            box.addItems(choices)
            box.setEditable(False)
            box.setMinimumHeight(self.ROW_HEIGHT - 4)
            # Open straight away: the cell is being edited precisely because a
            # different value is wanted, so the list should already be there.
            QtCore.QTimer.singleShot(0, box.showPopup)
            return box
        editor = FolderEdit(parent) if kind == "folder" \
            else QtWidgets.QLineEdit(parent)
        editor.setMinimumHeight(self.ROW_HEIGHT - 4)
        return editor

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

    def _tiled_grid(self, job, info):
        """
        The image a tiled job will actually produce, or None.

        Computed with the job's OWN pinned script rather than a copy of the
        maths here, so the number on screen cannot drift from the number the
        render uses. grid_preview caches the lifted compute_grid by script
        mtime, so this costs one ast parse per script and plain arithmetic per
        row after that — cheap enough for the 2s poll.
        """
        cfg = job.get("config") or {}
        key = (job.get("script"), cfg.get("LONG_EDGE"), cfg.get("TILE_LONG"),
               info.get("resolution_x"), info.get("resolution_y"),
               info.get("pixel_aspect_x"), info.get("pixel_aspect_y"))
        if key in self._grid:
            return self._grid[key]

        grid = None
        try:
            import grid_preview
            g = grid_preview.preview(job.get("script") or "", info,
                                     cfg.get("LONG_EDGE") or 120000,
                                     cfg.get("TILE_LONG") or 4096)
            if not g.get("error"):
                grid = g
        except Exception:
            pass                       # a missing script just shows "…"
        self._grid[key] = grid
        return grid

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

        self.verticalHeader().setDefaultSectionSize(CellEditor.ROW_HEIGHT)
        self.cellClicked.connect(self._explain_locked)
        self._ids = []
        self._pending = None                # refresh held back during an edit
        self._blend = {}                    # blend path -> (mtime, info)
        self._grid = {}                     # tiled job settings -> grid dict


    # ── data in ──────────────────────────────────────────────────────────

    @staticmethod
    def _mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0

    def set_blend_info(self, blend, info):
        self._blend[blend] = (self._mtime(blend), info)

    def blend_known(self, blend):
        """
        True only if the file has not changed since it was read.

        Keyed by modification time, not just path: saving the .blend in Blender
        has to invalidate this, or the app keeps showing the settings the file
        had when it was first seen. That is exactly what happened — an output
        path was changed in Blender, saved, and a new job still showed the old
        one.
        """
        hit = self._blend.get(blend)
        return bool(hit) and hit[0] == self._mtime(blend)

    def _info_for(self, blend):
        hit = self._blend.get(blend)
        return hit[1] if hit else None

    def selected_job_id(self):
        r = self.currentRow()
        return self._ids[r] if 0 <= r < len(self._ids) else None

    def select_job(self, job_id):
        """Put the cursor on a job by id. False if it is not in the table yet."""
        if job_id not in self._ids:
            return False
        self.selectRow(self._ids.index(job_id))
        return True

    def set_jobs(self, jobs):
        # Never rebuild while a cell is open for editing. The poller refreshes
        # every two seconds, and recreating the items underneath an editor
        # destroys it — which is why a field could only be typed in for a
        # moment before closing itself. The next refresh picks up whatever
        # changed once editing is finished.
        if self.state() == QtWidgets.QAbstractItemView.EditingState:
            self._pending = jobs
            return
        self._pending = None

        keep = self.selected_job_id()
        self._ids = [j["id"] for j in jobs]
        self.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            self._fill_row(row, job)
        if keep in self._ids:
            self.selectRow(self._ids.index(keep))

    def closeEditor(self, editor, hint):
        """Apply any refresh that was held back while the editor was open."""
        super().closeEditor(editor, hint)
        if self._pending is not None:
            pending, self._pending = self._pending, None
            QtCore.QTimer.singleShot(0, lambda: self.set_jobs(pending))

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
        info = self._info_for(job.get("blend")) or {}
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

        self._cell(row, "state", theme.state_label(job.get("state")), mono=True,
                   colour=theme.STATE_COLOUR.get(job.get("state"), theme.DIM))
        self._cell(row, "name", job.get("name", ""))

        # A .blend can hold several scenes, each with its own cameras,
        # resolution, frame range and output path. Every setting shown to the
        # right of this column belongs to the SELECTED scene, not to whichever
        # one happened to be active when the file was saved.
        scenes = info.get("scenes") or []
        names = [x.get("scene") for x in scenes if x.get("scene")]
        chosen = cfg.get("SCENE") or info.get("active_scene") or info.get("scene")
        # Falls back to `info` itself for a job whose .blend was probed by an
        # older version and cached without the per-scene breakdown.
        sinfo = next((x for x in scenes if x.get("scene") == chosen), info)
        multi = len(names) > 1

        self._cell(row, "scene", chosen or ("…" if unknown else "—"), mono=True,
                   editable=can_edit and multi,
                   job=job, cfg_key="SCENE", kind="choice", choices=names,
                   locked_why=(why if why else
                               "Reading the .blend…" if unknown else
                               "This .blend has only one scene" if not multi
                               else ""))

        classic = job.get("type") == "classic"
        cams = sinfo.get("cameras") or []
        cam = cfg.get("CAMERA") or sinfo.get("camera") or ("…" if unknown else "—")
        self._cell(row, "camera", cam, mono=True,
                   editable=can_edit and classic and bool(cams),
                   job=job, cfg_key="CAMERA", kind="choice", choices=cams,
                   locked_why=why or ("Reading the .blend…" if unknown else
                                      "The tiled script always uses the scene camera"))

        if unknown:
            res = samples = fmt = "…"
            res_why = "Reading the .blend…"
        elif classic:
            res = f"{sinfo.get('final_x')}×{sinfo.get('final_y')}"
            res_why = "Comes from the .blend"
            samples = str(sinfo.get("samples") or "—")
            fmt = cfg.get("FILE_FORMAT") or sinfo.get("file_format") or "—"
        else:
            # A tiled render never produces the .blend's resolution. The script
            # reads it for its ASPECT RATIO only, then sets the scene to one
            # tile (4096 px) per render and assembles the grid. Showing the
            # scene's own number here read as a promise the render would never
            # keep — a 2000x2000 @200% scene displayed "4000×4000" while every
            # tile on disk was 4096×4096 and the finished image 120000×120000.
            g = self._tiled_grid(job, sinfo)
            res = f"{g['total_w']}×{g['total_h']}" if g else "…"
            res_why = (
                f"{g['cols']}×{g['rows']} tiles of {g['tile_w']}×{g['tile_h']} px.\n"
                f"The .blend's own {sinfo.get('final_x')}×{sinfo.get('final_y')} is "
                f"used only for its aspect ratio."
                if g else "Set by the tiled grid")
            samples = str(sinfo.get("samples") or "—")
            fmt = cfg.get("FILE_FORMAT") or sinfo.get("file_format") or "—"
        self._cell(row, "resolution", res, mono=True, locked_why=res_why)
        self._cell(row, "samples", samples, mono=True,
                   locked_why="Comes from the .blend — never overridden")

        self._cell(row, "frames", self._frames_text(job, sinfo, unknown), mono=True,
                   editable=can_edit and classic, job=job,
                   cfg_key="_FRAMES", kind="text",
                   locked_why=why or "A tiled render is one frame")

        # Editable for both kinds now. A classic render still takes the
        # .blend's format by default; setting one here overrides it, which the
        # classic script has accepted since the 2026-07-27 snapshot. Older jobs
        # keep their pinned script and the server refuses, with a reason.
        self._cell(row, "format", fmt, mono=True,
                   editable=can_edit, job=job,
                   cfg_key="FILE_FORMAT", kind="choice",
                   choices=(["WEBP", "JPEG", "PNG"] if not classic else
                            ["PNG", "JPEG", "WEBP", "OPEN_EXR", "TIFF"]),
                   locked_why=why,
                   tip="Double-click to change — blank means the .blend's own"
                       if classic else "Double-click to change")

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
    def _first(*values):
        """
        First value that was actually given.

        Not `a or b`: frame 0 is a real frame number, and `or` treats it as
        missing — which showed "…" for a scene genuinely sitting on frame 0.
        """
        for v in values:
            if v is not None and v != "":
                return v
        return None

    @classmethod
    def _frames_text(cls, job, info, unknown):
        cfg = job.get("config") or {}
        if job.get("type") != "classic":
            return "—"
        if cfg.get("MODE") == "still":
            f = cls._first(cfg.get("FRAME"), info.get("frame_current"))
            return str(f) if f is not None else ("…" if unknown else "—")
        a = cls._first(cfg.get("FRAME_START"), info.get("frame_start"))
        b = cls._first(cfg.get("FRAME_END"), info.get("frame_end"))
        if a is None or b is None:
            return "…" if unknown else "—"
        return f"{a}-{b}"

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

    def mousePressEvent(self, e):
        """
        Clicking empty space clears the selection.

        Qt keeps the current row selected otherwise, so with a single job there
        was no way to deselect it at all.
        """
        if not self.indexAt(e.pos()).isValid():
            self.clearSelection()
            self.setCurrentIndex(QtCore.QModelIndex())
            e.accept()
            return
        super().mousePressEvent(e)

    def _explain_locked(self, row, col):
        """A locked cell should say why, not just refuse to open."""
        item = self.item(row, col)
        if item and not (item.flags() & QtCore.Qt.ItemIsEditable) \
                and item.toolTip():
            self.error.emit(item.toolTip())
