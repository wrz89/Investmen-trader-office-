@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Il bollettino di Leo adesso: soldi veri, classifica delle strategie, multiple virtuali e misure.
".venv\Scripts\python.exe" betbot.py bollettino
echo.
echo (Il bollettino e anche salvato in runtime\reports\bollettino.md)
pause
