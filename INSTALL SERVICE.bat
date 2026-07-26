@echo off
rem =============================================================================
rem Rendafar - install the startup task.
rem
rem Registers a Scheduled Task that launches "RUN SERVER LOOP.bat" every time
rem this user logs on. Combined with:
rem   1. BIOS  "Restore after AC Power Loss = Power On"
rem   2. Windows auto-login
rem the chain becomes: power returns -> PC boots -> auto-login -> task fires ->
rem Rendafar starts -> interrupted render auto-resumes -> Telegram notifies.
rem
rem Logon trigger (not "At startup") on purpose: Blender needs the user session
rem for GPU rendering, and auto-login is already part of the recovery chain.
rem
rem Run once. Re-running replaces the task. Remove with UNINSTALL SERVICE.bat.
rem =============================================================================

"%SystemRoot%\System32\schtasks.exe" /Create /F /TN "Rendafar" /SC ONLOGON /TR "\"%~dp0RUN SERVER LOOP.bat\""

if %errorlevel%==0 (
    echo.
    echo   Installed. Rendafar will start at every logon.
    echo   Start it now without rebooting:  schtasks /Run /TN "Rendafar"
) else (
    echo.
    echo   FAILED - try running this .bat as administrator.
)
echo.
pause
