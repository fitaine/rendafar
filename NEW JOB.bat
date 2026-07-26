@echo off
rem Rendafar - drag a .blend file onto this to queue a render.
title Rendafar - new job
cd /d "%~dp0"
set "PYTHON=python"
if exist "%~dp0local_python.bat" call "%~dp0local_python.bat"
"%PYTHON%" new_job.py %1
echo.
pause
