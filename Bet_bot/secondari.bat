@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Angoli e cartellini: regola le puntate finte del modello con i risultati e scrive il rapporto. Nessuna puntata vera.
".venv\Scripts\python.exe" betbot.py secondari
echo.
pause
