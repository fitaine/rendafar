@echo off
rem Removes the Rendafar startup task. Does not stop a server already running.
"%SystemRoot%\System32\schtasks.exe" /Delete /F /TN "Rendafar"
echo.
pause
