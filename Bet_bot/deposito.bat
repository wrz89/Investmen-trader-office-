@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Hai versato soldi su Betfair? Il bot li aggiunge al suo bankroll (capitale, non vincita). Chiede conferma SI.
".venv\Scripts\python.exe" betbot.py deposito
pause
