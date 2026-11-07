@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Tracker - avvio per i test (Ctrl+C per fermare)

rem Tutto tra parentesi: Windows legge il blocco intero prima di eseguirlo,
rem cosi' l'aggiornamento del codice (git pull) puo' cambiare anche questo file senza problemi.
(
  set "PY="
  py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PY=py -3"
  python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && if not defined PY set "PY=python"
  call :avvia %*
  echo.
  pause
  exit /b
)

:avvia
if not defined PY (
  echo.
  echo  Python 3 non trovato: installalo da https://www.python.org/downloads/
  echo  e durante l'installazione spunta "Add python.exe to PATH".
  exit /b 1
)
where git >nul 2>&1 || (
  echo  Git non trovato: senza git non posso scaricare gli aggiornamenti. Installalo da https://git-scm.com/
)
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo  Ho creato il file .env: compilalo, salva e chiudi il Blocco note.
  notepad ".env"
)
%PY% tools\avvio_test.py %*
exit /b
