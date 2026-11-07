@echo off
chcp 65001 >nul
setlocal
rem Ripristina valori standard di Windows (alla corrente): standby dopo 30 minuti, coperchio = sospendi
powercfg /change standby-timeout-ac 30
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /setactive SCHEME_CURRENT
echo.
echo  Fatto: alla corrente il PC va in standby dopo 30 minuti e quando chiudi il coperchio.
echo.
pause
