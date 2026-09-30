@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Test rapido di 6 ore: prezzi di betfair.it contro Pinnacle, nessuna puntata. Aspetta da solo la finestra con piu partite (test_rapido.bat subito = parte adesso). Lascia aperta questa finestra.
".venv\Scripts\python.exe" betbot.py test-rapido %1 --ore 6
pause
