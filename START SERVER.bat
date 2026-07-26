@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: Rendafar — starts the web server that owns the render queue.
::
:: Keep this window open: it supervises Blender, captures the render output,
:: and marks jobs finished when they end.  Closing it does NOT stop a render
:: in progress, but nothing will be watching it any more.
::
:: Phase 5 replaces this with a scheduled task that starts at boot.
:: ─────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"
echo.
echo   Starting Rendafar...
echo.
python -u server.py
echo.
echo   Server stopped. Press any key to close.
pause >nul
