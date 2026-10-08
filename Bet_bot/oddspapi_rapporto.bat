@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Rapporto dei lay dai dati OddsPapi gia scaricati: nessuna chiamata, istantaneo. Salvato anche in runtime\reports\oddspapi_storico.txt
".venv\Scripts\python.exe" betbot.py oddspapi-rapporto
echo.
pause
