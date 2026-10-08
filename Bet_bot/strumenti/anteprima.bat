@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Su cosa punterebbe adesso la strategia 4fun, con i prezzi veri di betfair.it. Nessuna puntata.
".venv\Scripts\python.exe" betbot.py anteprima
pause
