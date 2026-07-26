@echo off
rem Copy this to local_python.bat and set the absolute path to your Python.
rem
rem Why: the scheduled task runs in a fresh logon session which may not inherit
rem your user PATH, so a bare "python" can fail with exit code 9009 even though
rem it works fine in your terminal. Find yours with:  where python
set "PYTHON=C:\Users\you\miniforge3\python.exe"
