@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Cosa dice Betfair delle puntate vere ancora aperte nel bot (sola lettura).
".venv\Scripts\python.exe" betbot.py riconcilia
echo.
pause
