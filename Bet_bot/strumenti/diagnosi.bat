@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
if not exist ".venv\Scripts\python.exe" (
  echo Installazione non riuscita: leggi i messaggi sopra.
  pause
  exit /b 1
)
REM Controlla installazione, configurazione, chiavi e collegamenti.
".venv\Scripts\python.exe" betbot.py diagnosi
pause
