"""
What the Rendafar desktop icon runs.

Starts the server without a console window and opens the UI in its own
chrome-less window, so Rendafar behaves like an ordinary desktop app rather
than a script plus a browser tab.

.pyw, not .py: Windows runs it with pythonw.exe, which has no console. That is
the whole point — but it also means there is no window to close to stop the
app, so the UI carries a Quit button (POST /api/quit).

If Rendafar is already running this never starts a second server; it just opens
a window onto the one that exists. Two servers would fight over the port, and
worse, over the GPU.
"""

import glob
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

URL = f"http://127.0.0.1:{config.PORT}"

# Any Chromium browser supports --app, which gives a window with no address
# bar, no tabs, and its own taskbar entry.
#
# Looked up in the registry rather than guessed from hardcoded paths: this
# machine turned out to have no Edge at all, and hardcoding only worked by luck
# because Chrome sat where expected.
BROWSERS = ["msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe", "opera.exe"]

APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

FALLBACK_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
]


def find_browser():
    """First Chromium browser installed, from the registry then known paths."""
    try:
        import winreg
        for exe in BROWSERS:
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, f"{APP_PATHS}\\{exe}") as k:
                        path = winreg.QueryValue(k, None)
                    if path and os.path.isfile(path):
                        return path
                except OSError:
                    continue
    except ImportError:
        pass
    for path in FALLBACK_PATHS:
        if os.path.isfile(path):
            return path
    return None


def port_open(timeout=0.6):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", config.PORT)) == 0


def start_server():
    """Launch server.py with no console window."""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    env = os.environ.copy()
    # Tells the server it is an app, not a service: it should close itself once
    # the last window goes away and nothing is rendering, so the window's X
    # behaves the way an X should.
    env["RENDAFAR_APP_MODE"] = "1"
    subprocess.Popen(
        [pyw, os.path.join(config.APP_DIR, "server.py")],
        cwd=config.APP_DIR, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )


def wait_for_server(seconds=25):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if port_open():
            return True
        time.sleep(0.3)
    return False


APP_NAME = "Rendafar"

BROWSER_ROOTS = [
    ("Google", "Chrome", "User Data"),
    ("Microsoft", "Edge", "User Data"),
    ("BraveSoftware", "Brave-Browser", "User Data"),
]


def installed_app_id():
    """
    The browser's app id if Rendafar has been installed as a web app.

    Worth the trouble: a window opened with --app=<url> belongs to chrome.exe,
    so Windows shows CHROME's icon in the taskbar and groups it with Chrome. A
    window opened with --app-id=<installed id> belongs to the installed app,
    which has its own icon and its own taskbar identity.

    Found via the browser's "Web Applications" folder, which holds one
    `_crx_<app id>` directory per installed app containing `<App Name>.ico`.
    Chrome no longer records installed web apps in its Preferences file — that
    is where an earlier version of this looked, and why it always returned
    None.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    for parts in BROWSER_ROOTS:
        root = os.path.join(local, *parts)
        if not os.path.isdir(root):
            continue
        for folder in glob.glob(os.path.join(root, "*", "Web Applications",
                                             "_crx_*")):
            if os.path.isfile(os.path.join(folder, f"{APP_NAME}.ico")):
                return os.path.basename(folder)[len("_crx_"):]
    return None


def open_window(new_blend=None):
    """
    Open the UI in an app window, falling back to the default browser.

    A .blend path is passed through as ?new=, so dropping a file on the desktop
    icon opens the New render panel with it filled in — the one way to get a
    real file path, since a web page is never allowed to see one.
    """
    url = URL
    if new_blend:
        url += "/?new=" + urllib.parse.quote(new_blend)

    browser = find_browser()
    if browser:
        app_id = installed_app_id()
        # NO_WINDOW is a no-op for a GUI program like a browser, but every
        # launch in this project carries it so "does this flash a console?"
        # stays a question with one answer.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if app_id and not new_blend:
            # Installed app: correct icon, own taskbar identity.
            subprocess.Popen([browser, f"--app-id={app_id}"],
                             close_fds=True, creationflags=flags)
        else:
            subprocess.Popen([browser, f"--app={url}", "--window-size=1280,900"],
                             close_fds=True, creationflags=flags)
        return True
    import webbrowser
    webbrowser.open(url)
    return False


def error(msg):
    """No console here, so failures must be shown in a dialog."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Rendafar", 0x10)
    except Exception:
        pass


def main():
    # Windows hands a dropped file to the shortcut as an argument. This is the
    # only way to get a real path: a browser never reveals one, by design.
    new_blend = None
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".blend") and os.path.isfile(arg):
            new_blend = os.path.abspath(arg)
            break

    if not port_open():
        start_server()
        if not wait_for_server():
            error("Rendafar could not start.\n\n"
                  "Run START RENDAFAR.bat to see the error message.")
            return 1
    open_window(new_blend)
    return 0


if __name__ == "__main__":
    sys.exit(main())
