@echo off
REM Scarica lo storico e valida le strategie nuove (qualche minuto)
call .venv\Scripts\activate
python ufficio.py ricerca
pause
