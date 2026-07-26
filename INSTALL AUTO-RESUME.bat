@echo off
rem =============================================================================
rem Rendafar - install AUTO-RESUME.
rem
rem This does NOT make Rendafar start with Windows. Rendafar is a normal app:
rem double-click START RENDAFAR.bat when you want it, close it when you do not.
rem
rem What this installs is a small check that runs at logon and does nothing at
rem all - unless a render was interrupted. In that one case it starts Rendafar,
rem which resumes the render and sends you a Telegram message.
rem
rem Chain after a power cut:
rem   power returns -> BIOS powers the PC on -> Windows auto-login ->
rem   this check sees the unfinished render -> Rendafar starts -> resumes ->
rem   phone buzzes.
rem
rem Run once. Remove with UNINSTALL AUTO-RESUME.bat.
rem =============================================================================
"%SystemRoot%\System32\schtasks.exe" /Delete /F /TN "Rendafar" >nul 2>&1
"%SystemRoot%\System32\schtasks.exe" /Create /F /TN "Rendafar Auto-Resume" /SC ONLOGON /TR "\"%~dp0boot_check.bat\""
if %errorlevel%==0 (
    echo.
    echo   Auto-resume installed.
    echo   Rendafar will NOT start on its own, except to resume an interrupted render.
) else (
    echo.
    echo   FAILED - try running this .bat as administrator.
)
echo.
pause
