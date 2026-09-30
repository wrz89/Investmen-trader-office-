@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Quante partite hanno Pinnacle su The Odds API e quanti crediti servono al giorno (1 credito per campionato).
".venv\Scripts\python.exe" betbot.py copertura
pause
