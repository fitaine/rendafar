@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: Rendafar — server with crash-restart.
::
:: This is what the scheduled task launches at logon. If the server ever
:: crashes, it restarts after 10 seconds; renders themselves are unaffected by
:: a server crash (Blender is a separate process) but would run unsupervised,
:: so the loop closes that gap.
::
:: Close this window only if you mean to stop supervising renders.
:: ─────────────────────────────────────────────────────────────────────────────
title Rendafar
cd /d "%~dp0"

:loop
python -u server.py
echo.
echo   Rendafar stopped (exit %errorlevel%). Restarting in 10 s — Ctrl+C to abort.
timeout /t 10 >nul
goto loop
