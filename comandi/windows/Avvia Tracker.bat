@echo off
chcp 65001 >nul
setlocal
rem gli script stanno in comandi\windows: lavoro dalla cartella principale del progetto
cd /d "%~dp0..\.."
title Tracker - lascia aperta questa finestra

rem --- Cerca Python 3.9 o superiore ---
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo  Python 3 non trovato.
  echo  Installalo da https://www.python.org/downloads/  e durante l'installazione
  echo  spunta "Add python.exe to PATH". Poi riapri questo file.
  echo.
  pause
  exit /b 1
)

rem --- Primo avvio: crea il file .env ---
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo  Ho creato il file di configurazione .env
  echo  Controlla le impostazioni (email, ROOT_EMAIL...), salva e chiudi il Blocco note.
  echo.
  notepad ".env"
)

echo.
echo  Tracker in avvio... il browser si aprira' da solo.
echo  Per fermarlo chiudi questa finestra (o premi Ctrl+C).
echo.
set "OPEN_BROWSER=1"
%PY% server.py
echo.
pause
