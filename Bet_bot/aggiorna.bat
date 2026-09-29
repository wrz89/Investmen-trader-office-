@echo off
cd /d "%~dp0"
echo === Aggiornamento Bet_bot ===
echo I tuoi dati (cartella runtime) e l'installazione (.venv) non vengono toccati.
echo Ramo GitHub: master, oppure quello scritto in runtime\ramo.txt
pause
if exist "runtime\betbot.pid" (
  echo Chiudo Bet_bot acceso...
  for /f "usebackq delims=" %%P in ("runtime\betbot.pid") do (
    taskkill /F /PID %%P /FI "IMAGENAME eq python.exe" >nul 2>&1
    taskkill /F /PID %%P /FI "IMAGENAME eq pythonw.exe" >nul 2>&1
  )
  del "runtime\betbot.pid" >nul 2>&1
  timeout /t 2 /nobreak >nul
)
echo Scarico l'ultima versione da GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $b='master'; if (Test-Path 'runtime\ramo.txt') { $b=(Get-Content 'runtime\ramo.txt' -Raw).Trim() }; $z=Join-Path $env:TEMP 'betbot-update.zip'; $d=Join-Path $env:TEMP 'betbot-update'; $t=''; if (Test-Path 'runtime\github_token.txt') { $t=(Get-Content 'runtime\github_token.txt' -Raw).Trim() }; try { if ($t) { Invoke-WebRequest -UseBasicParsing -Headers @{Authorization=('Bearer ' + $t); 'User-Agent'='bet-bot'} ('https://api.github.com/repos/wrz89/Investmen-trader-office-/zipball/' + $b) -OutFile $z } else { Invoke-WebRequest -UseBasicParsing ('https://codeload.github.com/wrz89/Investmen-trader-office-/zip/refs/heads/' + $b) -OutFile $z } } catch { Write-Host 'ERRORE: download non riuscito. Se il repository e privato, metti un token di sola lettura in runtime\github_token.txt'; exit 1 }; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z -DestinationPath $d -Force; $src=Get-ChildItem $d -Directory | Select-Object -First 1; if (-not (Test-Path (Join-Path $src.FullName 'Bet_bot'))) { Write-Host 'ERRORE: nel ramo scaricato non c e la cartella Bet_bot'; exit 1 }"
if errorlevel 1 (pause & exit /b 1)
for /d %%D in ("%TEMP%\betbot-update\*") do robocopy "%%D\Bet_bot" "%~dp0." /E /XD runtime .venv /NFL /NDL /NJH /NJS /NP
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
echo.
echo Aggiornamento completato. Ora avvia avvia.bat
pause
