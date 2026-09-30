@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Spegne le puntate con soldi veri: al prossimo avvio il bot torna in paper.
".venv\Scripts\python.exe" betbot.py live off
pause
