@echo off
cd /d "%~dp0"
REM Elenca le coppie EUR/USDC piu' liquide su Bybit (per scegliere gli asset da testare)
if not exist ".venv\Scripts\python.exe" call installa.bat
".venv\Scripts\python.exe" ufficio.py mercati
pause
