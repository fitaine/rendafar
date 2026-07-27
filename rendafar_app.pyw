"""
Rendafar — desktop app entry point.

    pythonw rendafar_app.pyw          (no console)
    python  rendafar_app.pyw          (with console, for debugging)

Starts the server if it is not already running, then opens the window.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def die(message, detail=""):
    """No console in .pyw, so failures have to be shown in a dialog."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f"{message}\n\n{detail}".strip(), "Rendafar", 0x10)
    except Exception:
        print(message, detail)
    sys.exit(1)


def dropped_blend(argv):
    """
    A .blend dragged onto the desktop icon arrives as an argument.

    Windows passes the real path here — the same trick the browser version
    relied on, kept because dropping a file on the icon is a habit worth not
    breaking.
    """
    for arg in argv:
        if arg.lower().endswith(".blend") and os.path.isfile(arg):
            return os.path.abspath(arg)
    return None


def main():
    try:
        from PySide6 import QtWidgets
    except ImportError:
        die("The Rendafar desktop app needs PySide6.",
            "Install it with:\n\n    pip install PySide6\n\n"
            "The web interface and power-cut recovery work without it.")

    from desktop import api, theme
    from desktop.main_window import MainWindow

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Rendafar")
    # Its own AppUserModelID, or Windows groups the window under python.exe in
    # the taskbar and shows Python's icon instead of Rendafar's.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Buccino.Rendafar.Desktop.1")
    except Exception:
        pass

    theme.apply(app)

    if not api.ensure_server():
        die("Rendafar could not start its server.",
            "Run \"RUN SERVER LOOP.bat\" in a terminal to see why.")

    win = MainWindow()
    win.show()

    blend = dropped_blend(sys.argv[1:])
    if blend:
        # After show(), so the dialog has a window to sit on.
        from PySide6 import QtCore
        QtCore.QTimer.singleShot(250, lambda: win.do_new(blend))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
