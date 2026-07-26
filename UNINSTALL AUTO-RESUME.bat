@echo off
rem Removes the auto-resume check. Rendafar still works as a normal app;
rem it just will not come back by itself after a power cut.
"%SystemRoot%\System32\schtasks.exe" /Delete /F /TN "Rendafar Auto-Resume"
"%SystemRoot%\System32\schtasks.exe" /Delete /F /TN "Rendafar" >nul 2>&1
echo.
pause
