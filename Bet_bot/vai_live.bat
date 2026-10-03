@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Accende le puntate con SOLDI VERI (strategia 4fun: puntata fissa, max 10 al giorno). Chiede conferma scritta.
".venv\Scripts\python.exe" betbot.py live on
pause
