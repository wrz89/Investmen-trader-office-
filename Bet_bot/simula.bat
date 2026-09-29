@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
if not exist ".venv\Scripts\python.exe" (
  echo Installazione non riuscita: leggi i messaggi sopra.
  pause
  exit /b 1
)
REM 3 giorni di Bet_bot simulati in circa un minuto, poi la dashboard sulla simulazione.
".venv\Scripts\python.exe" betbot.py simula --ore 72
if errorlevel 1 (pause & exit /b 1)
".venv\Scripts\python.exe" betbot.py dashboard --simulazione
pause
