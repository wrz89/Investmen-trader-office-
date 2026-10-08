@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Quali sport ha betfair.it sul tuo conto (sola lettura) e quali Bet_bot legge gia.
".venv\Scripts\python.exe" betbot.py sport-disponibili
echo.
pause
