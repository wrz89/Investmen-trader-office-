@echo off
cd /d "%~dp0"
echo === Installazione Crypto Trading Office ===
echo Cartella: %cd%
echo.
REM Cerca Python: prima il launcher "py" di python.org, poi "python"
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo ERRORE: Python non trovato su questo PC.
  echo Installa Python da https://www.python.org/downloads/
  echo e nella prima schermata spunta "Add python.exe to PATH".
  echo Poi chiudi questa finestra e rilancia installa.bat
  pause
  exit /b 1
)
echo Python trovato:
%PY% --version
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Creo l'ambiente .venv ...
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo ERRORE: non riesco a creare l'ambiente .venv in %cd%
  echo Prova a spostare la cartella in C:\CryptoOffice e rilancia.
  pause
  exit /b 1
)
echo Installo le librerie (1-3 minuti)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERRORE durante l'installazione delle librerie. Controlla la connessione internet.
  pause
  exit /b 1
)
echo.
echo Installazione completata. Ora lancia ricerca.bat e poi avvia_ufficio.bat
pause
