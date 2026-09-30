@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Test rapido di 6 ore: prezzi di betfair.it contro Pinnacle, nessuna puntata. Lascia aperta questa finestra.
".venv\Scripts\python.exe" betbot.py test-rapido --ore 6
pause
