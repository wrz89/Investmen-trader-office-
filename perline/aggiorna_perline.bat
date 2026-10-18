@echo off
cd /d "%~dp0"
REM Installa o aggiorna Perline in questa cartella. I tuoi progetti (cartella "dati") non vengono toccati.
REM Per cambiare ramo GitHub modifica la riga qui sotto (ora "perline"; dopo il merge sara' "master").
set "RAMO=perline"
(
echo === Installazione / aggiornamento Perline ===
echo Cartella: %~dp0
echo I tuoi progetti ^(cartella dati^) non vengono toccati.
echo Scarico l'ultima versione da GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'perline-update.zip'; $d=Join-Path $env:TEMP 'perline-update'; $f='dati\github_token.txt'; $t=''; if (Test-Path $f) { $t=(Get-Content $f -Raw).Trim() }; try { if ($t) { Invoke-WebRequest -UseBasicParsing -Headers @{Authorization=('Bearer ' + $t); 'User-Agent'='perline'} ('https://api.github.com/repos/wrz89/Investmen-trader-office-/zipball/' + $env:RAMO) -OutFile $z } else { Invoke-WebRequest -UseBasicParsing ('https://codeload.github.com/wrz89/Investmen-trader-office-/zip/refs/heads/' + $env:RAMO) -OutFile $z } } catch { if ($t) { Write-Host 'ERRORE: GitHub rifiuta il token in dati\github_token.txt (serve il permesso Contents: Read-only su questo repository).' } else { Write-Host 'ERRORE: il repository su GitHub e privato. Crea il file dati\github_token.txt con un token di sola lettura (come per aggiorna.bat dell''ufficio).' }; exit 1 }; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z -DestinationPath $d -Force"
if errorlevel 1 (pause & exit /b 1)
for /d %%D in ("%TEMP%\perline-update\*") do robocopy "%%D\perline" "%~dp0." /E /XD dati tests /XF aggiorna_perline.bat /NFL /NDL /NJH /NJS /NP
echo.
echo Fatto. Avvia Perline con avvia_perline.bat
pause
exit /b 0
)
