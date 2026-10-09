@echo off
cd /d "%~dp0"
py -m compileall -q . || goto :fail
py self_test.py || goto :fail
py stress_test.py || goto :fail
echo TESTES OK
pause
exit /b 0
:fail
echo TESTES FALHARAM
pause
exit /b 1
