@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Quando conviene entrare: CLV e liquidita di betfair.it a 72, 48, 24, 12, 6, 3 e 1 ora dall inizio (dalle registrazioni).
".venv\Scripts\python.exe" betbot.py orizzonti
pause
