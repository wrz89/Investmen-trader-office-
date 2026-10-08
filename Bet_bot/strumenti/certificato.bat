@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
if not exist ".venv\Scripts\python.exe" (
  echo Installazione non riuscita: leggi i messaggi sopra.
  pause
  exit /b 1
)
REM Aggiorna le librerie (serve "cryptography") e crea il certificato per il login Betfair.
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" betbot.py certificato
explorer "runtime\betfair"
pause
