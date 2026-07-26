@echo off
rem =============================================================================
rem Rendafar - put the app on your Desktop and in the Start menu.
rem
rem Creates a shortcut with the Rendafar icon that opens the app in its own
rem window - no console, no browser bars. After running this you can also find
rem it by pressing Start and typing "Rendafar", and pin it to the taskbar.
rem
rem No administrator rights needed. Safe to run again to refresh the shortcuts.
rem =============================================================================
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_shortcuts.ps1"
echo.
pause
