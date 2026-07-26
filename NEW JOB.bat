@echo off
rem Rendafar - drag a .blend file onto this to queue a render.
title Rendafar - new job
cd /d "%~dp0"
python new_job.py %1
echo.
pause
