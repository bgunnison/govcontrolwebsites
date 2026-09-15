@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule\show_status.ps1"
if /I not "%~1"=="--no-pause" pause
