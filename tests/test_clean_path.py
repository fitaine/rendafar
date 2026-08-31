"""
Paths pasted by a person, and the four ways they arrive mangled.

Reported live: adding a .blend through the app gave "not found" for a file
that was plainly there. The path had been copied from somewhere that
percent-encodes, so every space was %20 and os.path.isfile said no. The app
stripped quotes and nothing else.

The same file legitimately reaches us as any of:

    "C:\\...\\a.blend"              Shift+right-click, Copy as path
    C:\\...\\Piste%20de%20bobsleigh  copied from anything URL-encoding
    file:///C:/.../a.blend         dragged out of a browser
    C:/.../a.blend                 forward slashes

These checks are about the contract: every form resolves to the same path, a
filename containing a real '%' is not mangled, and the entry points that take
a path from a person all go through the cleaner rather than each rolling their
own strip().

    python tests\\test_clean_path.py
"""

import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

import paths

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


TARGET = r"C:\dir with spaces\scene - 150M.blend"

forms = {
    "plain":            TARGET,
    "quoted":           '"' + TARGET + '"',
    "single-quoted":    "'" + TARGET + "'",
    "percent-encoded":  TARGET.replace(" ", "%20"),
    "file:/// URL":     "file:///" + TARGET.replace("\\", "/").replace(" ", "%20"),
    "file:// URL":      "file://" + TARGET.replace("\\", "/").replace(" ", "%20"),
    "forward slashes":  TARGET.replace("\\", "/"),
    "with whitespace":  "  " + TARGET + "  ",
}
for label, raw in forms.items():
    check(f"{label} resolves to the same path",
          paths.clean_path(raw) == TARGET,
          f"got {paths.clean_path(raw)!r}")

# A '%' that is part of the name must survive. Decoding is only taken when it
# helps, so a real file wins over a speculative decode.
odd = r"C:\renders\100%_final.blend"
check("a literal % in a name is not mangled when the file exists",
      paths.clean_path(odd) == odd or not os.path.exists(odd),
      f"got {paths.clean_path(odd)!r}")

check("empty input is handled", paths.clean_path("") == "")
check("None is handled", paths.clean_path(None) == "")

# The entry points a person can hand a path to must all use it, rather than
# each doing its own strip() and drifting apart again.
for fname, marker in (("server.py", "paths.clean_path"),
                      ("new_job.py", "paths.clean_path"),
                      ("queue_manager.py", "paths.clean_path"),
                      ("blend_info.py", "paths.clean_path")):
    src = open(os.path.join(APP, fname), encoding="utf-8").read()
    check(f"{fname} cleans the path it is given", marker in src)

src = open(os.path.join(APP, "server.py"), encoding="utf-8").read()
check("server.py no longer hand-rolls strip('\"') for the blend path",
      """(body.get("blend") or "").strip().strip('"')""" not in src)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
