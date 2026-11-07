@echo off
chcp 65001 >nul
setlocal
set "APPDIR=%~dp0"

rem Toglie il collegamento dall'Esecuzione automatica e ferma il tracker in background
powershell -NoProfile -ExecutionPolicy Bypass -Command "$f = Join-Path ([Environment]::GetFolderPath('Startup')) 'Tracker.lnk'; if (Test-Path $f) { Remove-Item $f }; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like ('*' + $env:APPDIR + 'server.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo.
echo  Avvio automatico disattivato e tracker in background fermato.
echo  Puoi sempre avviarlo a mano con "Avvia Tracker.bat".
echo.
pause
