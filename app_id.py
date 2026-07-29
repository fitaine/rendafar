"""
The app's Windows taskbar identity, in one place.

Windows decides which taskbar button a window belongs to — and which pinned
icon it merges with — by AppUserModelID, not by executable or window icon.
Two halves have to agree, or the taskbar misbehaves in ways that look like an
icon bug:

  * the PROCESS declares it, via SetCurrentProcessExplicitAppUserModelID,
    before any window exists (rendafar_app.pyw)
  * every SHORTCUT declares it, via the System.AppUserModel.ID property
    (install_shortcuts.ps1)

With only the first, pinning from the taskbar gives Python's icon: Windows
looks for a shortcut registered under this id, finds none, and falls back to
the host executable — which is pythonw.exe, shared with every other Python
program. With the shortcut half missing the window also refuses to merge with
a shortcut pinned from the Start menu, so launching the app puts a second
button next to the pinned one.

Kept here rather than written out twice because the two sides silently do
nothing useful if they ever drift apart. install_shortcuts.ps1 reads the
string out of this file rather than repeating it.
"""

APP_ID = "Buccino.Rendafar.Desktop.1"
