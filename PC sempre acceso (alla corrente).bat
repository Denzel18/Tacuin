@echo off
chcp 65001 >nul
setlocal
echo.
echo  Imposta il PC per restare acceso quando e' collegato alla corrente,
echo  cosi' il Tracker e il bot funzionano sempre (anche a coperchio chiuso).
echo  A batteria il PC continua ad andare in standby normalmente.
echo.
rem Alla corrente: mai standby, mai ibernazione
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
rem Alla corrente: chiudere il coperchio non fa nulla (0 = Non fare nulla)
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
if errorlevel 1 (
  echo  [!!] Non sono riuscito a cambiare le impostazioni: riprova con tasto destro ^> Esegui come amministratore.
) else (
  echo  Fatto! Alla corrente il PC non va piu' in standby, neanche chiudendo il coperchio.
  echo  Lo schermo si spegne comunque da solo per risparmiare energia.
  echo  Per tornare come prima: "Ripristina standby PC.bat".
)
echo.
pause
