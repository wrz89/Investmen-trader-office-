@echo off
cd /d "%~dp0"
REM Simulazione accelerata di 3 giorni dell'ufficio sportivo, poi la apre nella dashboard.
if not exist ".venv\Scripts\python.exe" call installa.bat
".venv\Scripts\python.exe" sport.py simula --ore 72
".venv\Scripts\python.exe" sport.py dashboard --simulazione
pause
