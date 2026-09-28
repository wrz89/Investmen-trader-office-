@echo off
REM Prima installazione (una volta sola). Serve Python 3.10+ da python.org
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Installazione completata. Ora lancia ricerca.bat e poi avvia_ufficio.bat
pause
