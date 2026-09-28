@echo off
cd /d "%~dp0"
echo === Diagnosi Crypto Trading Office ===
echo Cartella: %cd%
echo.
echo [py]
py -3 --version
echo [python]
python --version
echo [dove si trova python]
where python
where py
echo [ambiente .venv]
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" --version) else (echo .venv NON presente)
echo [file del progetto]
if exist "ufficio.py" (echo ufficio.py OK) else (echo ufficio.py NON trovato: sei nella cartella giusta?)
echo.
echo Fai una foto di questa finestra e mandala.
pause
