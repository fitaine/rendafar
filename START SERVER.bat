@echo off
rem Rendafar - start the web server by hand (the scheduled task is the normal way).
rem Keep this window open: it supervises Blender and finalises jobs.
title Rendafar
cd /d "%~dp0"
set "PYTHON=python"
if exist "%~dp0local_python.bat" call "%~dp0local_python.bat"
"%PYTHON%" -u server.py
echo.
echo   Server stopped. Press any key to close.
pause >nul
