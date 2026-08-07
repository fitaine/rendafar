"""
Selecting several jobs and moving them together.

Reordering a queue one row at a time is tedious once there are ten stills in
it, so rows can be picked with Ctrl and Shift and moved as a block.

The part worth testing is the reordering itself, which is NOT a loop over the
single-row move: applied one at a time the rows leapfrog each other and the
order that was the reason for selecting them is lost. Rows are taken in the
direction of travel, and one that has nowhere to go blocks the next, so a
selection stacks against the end of the queue instead of collapsing into it.

    python tests\test_multi_select.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

TMP = tempfile.mkdtemp(prefix="multisel_")
config.QUEUE_DIR = TMP
config.LOGS_DIR = os.path.join(TMP, "logs")
config.STATE_FILE = os.path.join(TMP, "state.json")
config.ensure_dirs()

import queue_manager as qm          # noqa: E402

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def move(order, picked, offset):
    qm.save_state({"order": list(order), "running": None, "running_pid": None})
    return "".join(qm.move_many(list(picked), offset))


print("\n=== 1. a block moves as a block ===")
check("two adjacent rows go up together",
      move("ABCDE", "BC", -1) == "BCADE", move("ABCDE", "BC", -1))
check("...and down together",
      move("ABCDE", "BC", +1) == "ADBCE", move("ABCDE", "BC", +1))
check("their order among themselves never changes",
      move("ABCDE", "BC", +1).index("B") < move("ABCDE", "BC", +1).index("C"),
      "this is the whole point of moving them together")

print("\n=== 2. rows that are not adjacent ===")
check("a gapped selection still moves",
      move("ABCDE", "BD", -1) == "BADCE", move("ABCDE", "BD", -1))
check("...and keeps its relative order",
      move("ABCDE", "BD", +1) == "ACBED", move("ABCDE", "BD", +1))

print("\n=== 3. the ends of the queue ===")
# A naive loop collapses the selection here: the first row cannot move, the
# second takes its place, and a block becomes a pile.
check("a selection already at the top stays put",
      move("ABCDE", "AB", -1) == "ABCDE", move("ABCDE", "AB", -1))
check("a selection already at the bottom stays put",
      move("ABCDE", "DE", +1) == "ABCDE", move("ABCDE", "DE", +1))
check("a stuck row does not stop the others",
      move("ABCDE", "AC", -1) == "ACBDE",
      "A is against the top; C still moves up.  " + move("ABCDE", "AC", -1))
check("...the same going down",
      move("ABCDE", "CE", +1) == "ABDCE", move("ABCDE", "CE", +1))
check("everything selected cannot go anywhere",
      move("ABCDE", "ABCDE", -1) == "ABCDE"
      and move("ABCDE", "ABCDE", +1) == "ABCDE")

print("\n=== 4. degenerate input ===")
check("an empty selection changes nothing",
      move("ABCDE", "", -1) == "ABCDE")
check("an offset of zero changes nothing",
      move("ABCDE", "BC", 0) == "ABCDE")
check("ids not in the queue are ignored",
      move("ABCDE", "BZ", -1) == "BACDE", move("ABCDE", "BZ", -1))

print("\n=== 5. one row still behaves as before ===")
check("a single row goes up", move("ABCDE", "C", -1) == "ABCDE".replace("BC", "CB"),
      move("ABCDE", "C", -1))
check("a single row goes down", move("ABCDE", "C", +1) == "ABDCE",
      move("ABCDE", "C", +1))

print("\n=== 6. the UI is wired to the whole selection ===")
from PySide6 import QtCore, QtWidgets                  # noqa: E402
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
from desktop import theme                              # noqa: E402
theme.apply(app)
from desktop.jobs_table import JobsTable               # noqa: E402

t = JobsTable()
t.resize(900, 300)
jobs = [{"id": f"job_{i}", "name": chr(64 + i), "type": "classic",
         "state": "queued", "blend": "x.blend", "script": "s",
         "config": {"MODE": "still"}} for i in range(1, 6)]
t.set_jobs(jobs)
app.processEvents()

check("the table allows more than one row",
      t.selectionMode() == QtWidgets.QAbstractItemView.ExtendedSelection,
      t.selectionMode().name)

t.selectRow(1)
t.selectionModel().select(
    t.model().index(3, 0),
    QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
app.processEvents()
check("selected ids come back in queue order, not click order",
      t.selected_job_ids() == ["job_2", "job_4"], str(t.selected_job_ids()))

t.clearSelection()
app.processEvents()
check("no selection is an empty list", t.selected_job_ids() == [])

import inspect                                         # noqa: E402
import desktop.main_window as mw                       # noqa: E402
src = inspect.getsource(mw.MainWindow.do_move)
check("do_move sends the whole selection in ONE call",
      "selected_job_ids()" in src and "api.move, ids" in src,
      "one call per row would leapfrog them and half-apply on a failure")
check("Remove acts on the selection too",
      "selected_job_ids()" in inspect.getsource(mw.MainWindow.do_remove))
check("...and says how many it is about to remove",
      "these {len(ids)} jobs" in inspect.getsource(mw.MainWindow.do_remove))

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
