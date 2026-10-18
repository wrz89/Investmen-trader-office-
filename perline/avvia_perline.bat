@echo off
cd /d "%~dp0"
REM Avvia Perline (serve solo Python, nessuna libreria da installare).
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo ERRORE: Python non trovato. Installalo da https://www.python.org/downloads/
  echo spuntando "Add python.exe to PATH", poi rilancia questo file.
  pause
  exit /b 1
)
%PY% perline.py
pause
