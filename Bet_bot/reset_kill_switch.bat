@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Sblocca il kill switch (dopo aver controllato il conto su Betfair) e riallinea il bankroll al saldo vero.
".venv\Scripts\python.exe" betbot.py reset-kill-switch
echo.
pause
