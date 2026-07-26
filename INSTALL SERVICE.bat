@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: Rendafar — install the startup task.
::
:: Registers a Scheduled Task that launches "RUN SERVER LOOP.bat" every time
:: this user logs on.  Combined with:
::   1. BIOS  "Restore after AC Power Loss = Power On"
::   2. Windows auto-login
:: the chain becomes: power returns → PC boots → auto-login → this task fires →
:: Rendafar starts → interrupted render auto-resumes → Telegram notifies.
::
:: Logon trigger (not "At startup") on purpose: Blender needs the user session
:: for GPU rendering, and auto-login is already part of the recovery chain.
::
:: Run once. Re-running replaces the task. Remove with UNINSTALL SERVICE.bat.
:: ─────────────────────────────────────────────────────────────────────────────

schtasks /Create /F /TN "Rendafar" /SC ONLOGON /TR "\"%~dp0RUN SERVER LOOP.bat\""

if %errorlevel%==0 (
    echo.
    echo   Installed. Rendafar will start at every logon.
    echo   Start it now without rebooting:  schtasks /Run /TN "Rendafar"
) else (
    echo.
    echo   FAILED — try running this .bat as administrator.
)
echo.
pause
