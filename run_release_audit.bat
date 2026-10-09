@echo off
cd /d "%~dp0"
py release_audit.py || goto :fail
py asset_integrity.py || goto :fail
echo AUDITORIA OK
pause
exit /b 0
:fail
echo AUDITORIA FALHOU
pause
exit /b 1
