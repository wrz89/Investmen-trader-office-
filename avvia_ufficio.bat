@echo off
cd /d "%~dp0"
REM Avvia l'ufficio in PAPER TRADING e apre la dashboard nel browser.
REM Lascia aperta questa finestra: chiudendola l'ufficio si ferma.
call .venv\Scripts\activate
python ufficio.py avvia
pause
