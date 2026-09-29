@echo off
cd /d "%~dp0"
echo === Installazione Bet_bot ===
echo Cartella: %cd%
echo.
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo ERRORE: Python non trovato. Installa Python 3.10 o superiore da https://www.python.org/downloads/
  echo e nella prima schermata spunta "Add python.exe to PATH". Poi rilancia installa.bat
  pause
  exit /b 1
)
%PY% --version
if not exist ".venv\Scripts\python.exe" (
  echo Creo lambiente .venv ...
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo ERRORE: non riesco a creare lambiente .venv in %cd%
  pause
  exit /b 1
)
echo Installo le librerie (1-3 minuti)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERRORE durante linstallazione delle librerie. Controlla la connessione internet.
  pause
  exit /b 1
)
echo.
echo Installazione completata. Prossimi passi: simula.bat, backtest.bat, poi avvia.bat
pause
