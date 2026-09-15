@echo off
setlocal
cd /d "%~dp0"

python update_all.py
set "result=%errorlevel%"

if not "%result%"=="0" (
    echo.
    echo Update stopped because a site failed.
    echo No server upload was attempted. This command never uploads website files.
)

if /I not "%~1"=="--no-pause" (
    echo.
    pause
)

exit /b %result%
