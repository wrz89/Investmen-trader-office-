@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Tennis: statistiche dei giocatori (Jeff Sackmann) contro le quote di tennis-data. Sola lettura, nessuna puntata.
".venv\Scripts\python.exe" betbot.py tennis-lab
echo.
pause
