@echo off
:: Removes the Rendafar startup task. Does not stop a server already running.
schtasks /Delete /F /TN "Rendafar"
echo.
pause
