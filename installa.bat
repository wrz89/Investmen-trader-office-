@echo off
cd /d "%~dp0"
echo === Installazione Crypto Trading Office ===
REM Cerca Python: prima "py" (launcher di python.org), poi "python"
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY python --version >nul 2>&1 && set PY=python
if not defined PY (
  echo.
  echo ERRORE: Python non trovato. Reinstallalo da python.org
  echo spuntando "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Uso: %PY%
%PY% -m venv .venv
if errorlevel 1 ( echo ERRORE nella creazione dell'ambiente. & pause & exit /b 1 )
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 ( echo ERRORE nell'installazione delle librerie. & pause & exit /b 1 )
echo.
echo Installazione completata. Ora lancia ricerca.bat e poi avvia_ufficio.bat
pause
