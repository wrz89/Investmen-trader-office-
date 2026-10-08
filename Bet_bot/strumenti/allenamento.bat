@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Le strategie rigiocano 5 anni senza vedere il risultato; Leo fa l autopsia di ogni puntata persa.
".venv\Scripts\python.exe" betbot.py allenamento
pause
