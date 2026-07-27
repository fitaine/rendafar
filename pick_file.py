"""
Opens a real Windows file dialog and prints the chosen path.

Run as a separate short-lived process rather than inside the server, on
purpose: Tk is not thread-safe, and the server answers requests on worker
threads. A dedicated process sidesteps that entirely and cannot wedge the
server if the dialog misbehaves.

    python pick_file.py [initial_directory]

Prints the selected path on stdout, or nothing if cancelled.
"""

import os
import sys


def main():
    import tkinter as tk
    from tkinter import filedialog

    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    if initial and not os.path.isdir(initial):
        initial = os.path.dirname(initial)
    if not initial or not os.path.isdir(initial):
        initial = os.path.expanduser("~")

    root = tk.Tk()
    root.withdraw()
    # Without this the dialog can open behind the app window, which looks
    # exactly like nothing happened.
    root.attributes("-topmost", True)

    path = filedialog.askopenfilename(
        parent=root,
        title="Choose a .blend file",
        initialdir=initial,
        filetypes=[("Blender scene", "*.blend"), ("All files", "*.*")],
    )
    root.destroy()

    if path:
        sys.stdout.write(os.path.normpath(path))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(1)
