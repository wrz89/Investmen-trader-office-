@echo off
cd /d "%~dp0"
(
echo === Aggiornamento Crypto Trading Office ===
echo I tuoi dati ^(cartella runtime^) e l'installazione ^(.venv^) non vengono toccati.
echo Se l'ufficio e' acceso, chiudi prima la sua finestra nera.
pause
echo Scarico l'ultima versione da GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'cto-update.zip'; $d=Join-Path $env:TEMP 'cto-update'; Invoke-WebRequest -UseBasicParsing 'https://codeload.github.com/wrz89/Investmen-trader-office-/zip/refs/heads/master' -OutFile $z; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z -DestinationPath $d -Force"
if errorlevel 1 (echo ERRORE: download non riuscito, controlla la connessione. & pause & exit /b 1)
for /d %%D in ("%TEMP%\cto-update\*") do robocopy "%%D" "%~dp0." /E /XD runtime .venv /NFL /NDL /NJH /NJS /NP
echo.
echo Aggiornamento completato. Ora avvia avvia_ufficio.bat
pause
exit /b 0
)
