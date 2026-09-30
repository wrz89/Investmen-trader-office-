@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Esame per il live: quali strategie hanno superato i criteri sui prezzi veri di betfair.it.
".venv\Scripts\python.exe" betbot.py esame
pause
