@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Lay di valore sullo storico OddsPapi (sola lettura). Ogni lancio scarica fino a 40 partite nuove (5 s l'una): rilancialo piu volte.
".venv\Scripts\python.exe" betbot.py oddspapi-storico
echo.
pause
