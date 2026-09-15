@echo off
setlocal
cd /d "%~dp0"

python view_sites.py
set "result=%errorlevel%"

if not "%result%"=="0" (
    echo.
    echo The local previews could not be opened.
)

exit /b %result%
