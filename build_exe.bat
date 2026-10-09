@echo off
cd /d "%~dp0"
py -m pip install -r requirements-dev.txt || goto :fail
py release_audit.py || goto :fail
py asset_integrity.py || goto :fail
py self_test.py || goto :fail
py stress_test.py || goto :fail
py -m PyInstaller --noconfirm riftwalker.spec || goto :fail
echo Build pronta em dist\
pause
exit /b 0
:fail
echo BUILD FALHOU
pause
exit /b 1
