@echo off
cd /d "%~dp0"
(
echo === Aggiornamento Crypto Trading Office ===
echo I tuoi dati ^(cartella runtime^) e l'installazione ^(.venv^) non vengono toccati.
pause
if exist "runtime\office.pid" (
  echo Chiudo l'ufficio acceso ^(anche quello nascosto dell'avvio automatico^)...
  for /f "usebackq delims=" %%P in ("runtime\office.pid") do (
    taskkill /F /PID %%P /FI "IMAGENAME eq python.exe" >nul 2>&1
    taskkill /F /PID %%P /FI "IMAGENAME eq pythonw.exe" >nul 2>&1
  )
  del "runtime\office.pid" >nul 2>&1
  echo riavvia> "%TEMP%\cto-restart.flag"
  timeout /t 2 /nobreak >nul
)
echo Scarico l'ultima versione da GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'cto-update.zip'; $d=Join-Path $env:TEMP 'cto-update'; $f='runtime\github_token.txt'; $t=''; if (Test-Path $f) { $t=(Get-Content $f -Raw).Trim() }; try { if ($t) { Invoke-WebRequest -UseBasicParsing -Headers @{Authorization=('Bearer ' + $t); 'User-Agent'='crypto-trading-office'} 'https://api.github.com/repos/wrz89/Investmen-trader-office-/zipball/master' -OutFile $z } else { Invoke-WebRequest -UseBasicParsing 'https://codeload.github.com/wrz89/Investmen-trader-office-/zip/refs/heads/master' -OutFile $z } } catch { if ($t) { Write-Host 'ERRORE: GitHub rifiuta il token. Controlla runtime\github_token.txt (permesso Contents: Read-only su questo repository).' } else { Write-Host 'ERRORE: il repository su GitHub e privato. Rendilo pubblico oppure crea il file runtime\github_token.txt con un token di sola lettura (istruzioni nel README).' }; exit 1 }; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z -DestinationPath $d -Force"
if errorlevel 1 (pause & exit /b 1)
for /d %%D in ("%TEMP%\cto-update\*") do robocopy "%%D" "%~dp0." /E /XD runtime .venv /NFL /NDL /NJH /NJS /NP
echo.
if exist "%TEMP%\cto-restart.flag" (
  del "%TEMP%\cto-restart.flag" >nul 2>&1
  echo Aggiornamento completato. Riavvio l'ufficio con la versione nuova...
  start "" "%~dp0avvia_ufficio.bat"
) else (
  echo Aggiornamento completato. Ora avvia avvia_ufficio.bat
)
pause
exit /b 0
)
