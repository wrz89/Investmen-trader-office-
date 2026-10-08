@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Prova OddsPapi (sola lettura, nessuna puntata): slug Pinnacle/Betfair, esiti 1X2, freschezza delle quote.
".venv\Scripts\python.exe" betbot.py oddspapi
echo.
pause
