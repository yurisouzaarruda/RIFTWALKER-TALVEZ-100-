@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt || goto :fail
py release_audit.py || goto :fail
py asset_integrity.py || goto :fail
py self_test.py || goto :fail
py stress_test.py || goto :fail
py main.py
exit /b %errorlevel%
:fail
echo Falha na instalacao/auditoria. Veja a mensagem acima.
pause
exit /b 1
