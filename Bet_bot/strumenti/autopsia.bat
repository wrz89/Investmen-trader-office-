@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call installa.bat
REM Autopsia di una puntata: scrivi un nome (es. Berrettini) o il numero; vuoto = l ultima chiusa.
set /p CHI="Nome o numero della puntata (Invio = ultima chiusa): "
".venv\Scripts\python.exe" betbot.py autopsia %CHI%
pause
