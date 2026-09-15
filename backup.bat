@echo off
setlocal
cd /d "%~dp0"

python backup_all.py %*
set "result=%errorlevel%"

if not "%result%"=="0" (
    echo.
    echo Backup incomplete. Check the error above. Existing backups were kept.
)

set "backup_pause=1"
:check_args
if "%~1"=="" goto finish
if /I "%~1"=="--no-pause" set "backup_pause=0"
shift
goto check_args

:finish
if "%backup_pause%"=="1" (
    echo.
    pause
)
exit /b %result%
