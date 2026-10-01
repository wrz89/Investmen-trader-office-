@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Legge lo storico ufficiale Betfair messo in runtime\storico_betfair (archivio .tar del piano Basic).
if not exist "runtime\storico_betfair" mkdir "runtime\storico_betfair"
".venv\Scripts\python.exe" betbot.py storico-betfair
pause
