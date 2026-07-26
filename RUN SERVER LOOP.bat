@echo off
rem =============================================================================
rem Rendafar - server with crash-restart.
rem
rem This is what the scheduled task launches at logon. If the server ever
rem crashes, it restarts after 10 seconds; renders themselves are unaffected by
rem a server crash (Blender is a separate process) but would run unsupervised,
rem so the loop closes that gap.
rem
rem Close this window only if you mean to stop supervising renders.
rem =============================================================================
title Rendafar
cd /d "%~dp0"

rem Resolve Python. A scheduled task starts in a fresh logon session that may
rem not inherit the user PATH, so a bare "python" can fail with exit 9009.
rem local_python.bat (gitignored) pins an absolute path - see the .example.
set "PYTHON=python"
if exist "%~dp0local_python.bat" call "%~dp0local_python.bat"

:loop
"%PYTHON%" -u server.py
echo.
echo   Rendafar stopped (exit %errorlevel%). Restarting in 10 s - Ctrl+C to abort.
"%SystemRoot%\System32\timeout.exe" /t 10 >nul
goto loop
