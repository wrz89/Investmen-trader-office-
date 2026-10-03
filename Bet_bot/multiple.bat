@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Le multiple virtuali (doppie e triple) dalle puntate di misura.
".venv\Scripts\python.exe" betbot.py multiple
pause
