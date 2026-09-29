@echo off
cd /d "%~dp0"
REM Avvia Bet_bot. Lascia aperta questa finestra: chiudendola il bot si ferma.
REM Per spegnerlo in modo ordinato (trade chiusi): python betbot.py ferma, oppure CTRL+C.
if not exist ".venv\Scripts\python.exe" call installa.bat
if not exist ".venv\Scripts\python.exe" (
  echo Installazione non riuscita: leggi i messaggi sopra.
  pause
  exit /b 1
)
set "ARGS=%*"
:loop
".venv\Scripts\python.exe" betbot.py avvia %ARGS%
set "CODE=%errorlevel%"
if exist "runtime\ferma.richiesta" (del "runtime\ferma.richiesta" & goto fine)
if "%CODE%"=="0" goto fine
echo.
echo Bet_bot si e' chiuso in modo inatteso (codice %CODE%). Riparto tra 30 secondi (CTRL+C per annullare)...
REM attesa e controllo sulla stessa riga: `betbot.py ferma` o aggiorna.bat durante l'attesa fermano il riavvio
timeout /t 30 >nul & if exist "runtime\ferma.richiesta" (del "runtime\ferma.richiesta" & goto fine)
set "ARGS=--no-browser"
goto loop
:fine
echo Bet_bot fermato.
if "%ARGS%"=="" pause
