@echo off
cd /d "%~dp0"

set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY (
    python --version >nul 2>&1 && set PY=python
)
if defined PY goto run

echo Python isn't installed.
where winget >nul 2>&1
if errorlevel 1 (
    echo Install Python from https://www.python.org/downloads/ and run this again.
    pause
    exit /b 1
)
set /p ANS=Install it now with winget? [Y/N] 
if /i not "%ANS%"=="Y" exit /b 1
winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
echo.
echo Done. Close this window and double-click run.bat again.
pause
exit /b 0

:run
%PY% video_resizer.py %*
if errorlevel 1 pause
