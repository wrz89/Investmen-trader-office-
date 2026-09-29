@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
if not exist ".venv\Scripts\python.exe" (
  echo Installazione non riuscita: leggi i messaggi sopra.
  pause
  exit /b 1
)
REM Avvia Bet_bot. Lascia aperta questa finestra: chiudendola il bot si ferma.
".venv\Scripts\python.exe" betbot.py avvia
pause
