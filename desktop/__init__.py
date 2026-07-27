"""
The Rendafar desktop app (PySide6).

A client of server.py, not a replacement for it. The server keeps owning the
queue and Blender, which is what lets a render survive this window closing,
keeps the phone working while the desktop app is shut, and keeps power-cut
recovery working without Qt installed at all.
"""
