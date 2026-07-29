"""
The Windows taskbar identity, and the fact that it takes TWO agreeing halves.

Reported live: pinning from the taskbar gave Python's icon, and pinning from
the Start menu gave a second button beside the pinned one when the app opened.
Both are the same cause. The process declared an AppUserModelID but the
shortcuts declared none, so Windows identified them by their target —
pythonw.exe, shared with every other Python program — and never matched the
two up.

These checks are deliberately about the CONTRACT rather than about Windows
behaviour, which cannot be exercised from a test: that the id exists in one
place, that neither side hardcodes its own copy, and that the .ico carries the
sizes the taskbar actually draws.

    python tests\test_app_id.py
"""

import os
import re
import struct
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

import app_id

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


launcher = open(os.path.join(APP, "rendafar_app.pyw"), encoding="utf-8").read()
ps1 = open(os.path.join(APP, "install_shortcuts.ps1"), encoding="utf-8").read()

print("\n=== 1. one id, defined once ===")
check("app_id.APP_ID is a non-empty string",
      isinstance(app_id.APP_ID, str) and app_id.APP_ID.strip() != "")
check("it looks like an AppUserModelID (dotted, no spaces)",
      "." in app_id.APP_ID and " " not in app_id.APP_ID, app_id.APP_ID)
check("it is under the 128 char limit Windows imposes",
      len(app_id.APP_ID) <= 128, f"{len(app_id.APP_ID)} chars")

print("\n=== 2. the process half ===")
check("the launcher sets an explicit AppUserModelID",
      "SetCurrentProcessExplicitAppUserModelID" in launcher)
check("...taking it from app_id rather than repeating the string",
      "from app_id import APP_ID" in launcher
      and f'"{app_id.APP_ID}"' not in launcher,
      "a second copy of the id is how these two halves silently drift apart")
check("...before the QApplication is constructed",
      launcher.index("SetCurrentProcessExplicitAppUserModelID")
      < launcher.index("QtWidgets.QApplication(sys.argv)"),
      "Windows binds the identity when the first window appears; setting it "
      "afterwards is too late")

print("\n=== 3. the shortcut half ===")
check("the installer stamps System.AppUserModel.ID",
      "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3" in ps1,
      "the property key for System.AppUserModel.ID")
check("...via IPropertyStore, which is the only way to set it",
      "IPropertyStore" in ps1,
      "WScript.Shell exposes TargetPath and IconLocation but not this")
check("...reading the id out of app_id.py rather than repeating it",
      "app_id.py" in ps1 and f'"{app_id.APP_ID}"' not in ps1)

check("the installer's APP_ID regex actually matches app_id.py",
      bool(re.search(r'APP_ID\s*=\s*"(.+)"',
                     open(os.path.join(APP, "app_id.py"), encoding="utf-8").read())),
      "if this pattern stops matching, the installer throws rather than "
      "silently writing a wrong id")

check("it also fixes an already-pinned taskbar shortcut",
      "User Pinned" in ps1,
      "a taskbar pin is its own .lnk and inherits nothing from the Start "
      "menu copy, so without this the fix looks like it did not work")
check("it verifies the write instead of assuming it stuck",
      "GetAppId" in ps1 and "did not stick" in ps1,
      "a silent no-op here is indistinguishable from a Windows icon cache "
      "problem, and sends the next person hunting in the wrong place")

print("\n=== 4. the icon the taskbar will draw ===")
ico = os.path.join(APP, "static", "rendafar.ico")
check("static/rendafar.ico exists", os.path.isfile(ico),
      "install_shortcuts.ps1 refuses to run without it")

if os.path.isfile(ico):
    data = open(ico, "rb").read()
    count = struct.unpack("<H", data[4:6])[0]
    sizes = set()
    for i in range(count):
        off = 6 + i * 16
        sizes.add((data[off] or 256, data[off + 1] or 256))
    check("it carries 32x32, the size the taskbar draws",
          (32, 32) in sizes, f"has {sorted(sizes)}")
    check("...and 16x16 for the window corner and Alt-Tab",
          (16, 16) in sizes, f"has {sorted(sizes)}")
    check("...and 256x256 for large icon views",
          (256, 256) in sizes, f"has {sorted(sizes)}")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
