@echo off
chcp 65001 >nul
setlocal
rem gli script stanno in comandi\windows: lavoro dalla cartella principale del progetto
cd /d "%~dp0..\.."

set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo  Python 3 non trovato: installalo da https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".env" (
  echo  Prima avvia una volta "comandi\windows\Avvia Tracker.bat" per creare il file .env
  pause
  exit /b 1
)

rem pythonw.exe = Python senza finestra, gira in background
for /f "usebackq delims=" %%p in (`%PY% -c "import sys; print(sys.executable)"`) do set "PYEXE=%%p"
for %%d in ("%PYEXE%") do set "PYW=%%~dpdpythonw.exe"
if not exist "%PYW%" (
  echo  Non trovo pythonw.exe: reinstalla Python da python.org
  pause
  exit /b 1
)
set "APPDIR=%CD%\"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$l = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\Tracker.lnk'); $l.TargetPath = $env:PYW; $l.Arguments = [char]34 + $env:APPDIR + 'server.py' + [char]34; $l.WorkingDirectory = $env:APPDIR; $l.Description = 'Tracker - server e bot Telegram'; $l.Save()"
if errorlevel 1 (
  echo  Non sono riuscito a creare il collegamento di avvio automatico.
  pause
  exit /b 1
)

rem Avvialo subito in background (se e' gia' avviato, la seconda copia si chiude da sola)
start "" "%PYW%" "%APPDIR%server.py"

set "PORT=8080"
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="PORT" if not "%%b"=="" set "PORT=%%b"
timeout /t 2 /nobreak >nul
start "" "http://localhost:%PORT%"

echo.
echo  Fatto! Il tracker ora parte da solo, in background, ogni volta che accedi a Windows.
echo  Indirizzo: http://localhost:%PORT%
echo  Log:       %APPDIR%data\server.log
echo  Per toglierlo usa "comandi\windows\Disattiva avvio automatico.bat".
echo.
pause
