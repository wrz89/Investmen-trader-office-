@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Perche il bot non punta: freni attivi, motivi dei veti delle ultime ore, lezioni di Leo.
".venv\Scripts\python.exe" betbot.py perche
echo.
pause
