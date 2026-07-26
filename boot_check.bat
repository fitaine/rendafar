@echo off
rem Runs at logon via the "Rendafar Auto-Resume" task.
rem Silent and does nothing - unless a render was interrupted, in which case
rem it starts Rendafar so the render resumes.
cd /d "%~dp0"
set "PYTHON=python"
if exist "%~dp0local_python.bat" call "%~dp0local_python.bat"
"%PYTHON%" boot_check.py
