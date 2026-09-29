@echo off
cd /d "%~dp0"
REM Scarica lo storico reale (football-data.co.uk) e confronta le strategie sportive.
if not exist ".venv\Scripts\python.exe" call installa.bat
".venv\Scripts\python.exe" sport.py backtest
pause
