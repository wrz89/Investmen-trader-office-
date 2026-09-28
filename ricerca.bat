@echo off
cd /d "%~dp0"
REM Scarica lo storico e valida le strategie nuove (qualche minuto)
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente non ancora installato: avvio installa.bat ...
  call installa.bat
)
if not exist ".venv\Scripts\python.exe" (
  echo Installazione non riuscita: leggi i messaggi sopra.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" ufficio.py ricerca
pause
