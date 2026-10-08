@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
if not exist ".venv\Scripts\python.exe" (
  echo Installazione non riuscita: leggi i messaggi sopra.
  pause
  exit /b 1
)
REM Scarica lo storico reale e confronta le strategie (la prima volta ci mette qualche minuto).
".venv\Scripts\python.exe" betbot.py backtest
pause
