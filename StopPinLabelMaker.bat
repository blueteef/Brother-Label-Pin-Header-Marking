@echo off
rem Stops the background Pin Label Maker started by PinLabelMaker.vbs.
set "PORT=8501"
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    taskkill /PID %%p /F >nul 2>nul
    set "FOUND=1"
)
if defined FOUND (echo Pin Label Maker stopped.) else (echo Pin Label Maker was not running.)
ping -n 3 127.0.0.1 >nul
