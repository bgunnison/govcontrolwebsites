@echo off
setlocal
cd /d "%~dp0"

python deploy_all.py
set "result=%errorlevel%"

if not "%result%"=="0" (
    echo.
    echo Deployment stopped. Check the error above; credentials were not displayed.
)

if /I not "%~1"=="--no-pause" (
    echo.
    pause
)

exit /b %result%
