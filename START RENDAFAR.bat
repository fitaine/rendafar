@echo off
rem =============================================================================
rem RENDAFAR - double-click to start the app.
rem
rem Opens the queue server and the web UI at http://127.0.0.1:8765
rem Closing this window stops Rendafar. Blender keeps rendering if it was
rem mid-tile, but nothing will be supervising it.
rem
rem You do NOT need to leave this running all the time. If a render is cut off
rem by a power failure, Rendafar restarts itself at the next logon and picks
rem the render back up - see INSTALL AUTO-RESUME.bat.
rem =============================================================================
title Rendafar
cd /d "%~dp0"
set "PYTHON=python"
if exist "%~dp0local_python.bat" call "%~dp0local_python.bat"
echo.
echo   Rendafar starting - http://127.0.0.1:8765
echo   Close this window to stop.
echo.
start "" http://127.0.0.1:8765
:loop
"%PYTHON%" -u server.py
echo.
echo   Rendafar stopped (exit %errorlevel%). Restarting in 10 s - close this window to quit.
"%SystemRoot%\System32\timeout.exe" /t 10 >nul
goto loop
