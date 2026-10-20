@echo off
rem Launch Pin Label Maker.
rem   PinLabelMaker.bat          visible console (good for seeing errors)
rem   PinLabelMaker.bat hidden   no console output; used by PinLabelMaker.vbs
rem The server only listens on this computer (127.0.0.1): the app can print to your printer.
rem Uses .venv if present, else a Python 3.12/3.13 that already has streamlit, else `python`.
cd /d "%~dp0"
set "PORT=8501"
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (py -3.12 -c "import streamlit" >nul 2>nul && set "PY=py -3.12")
if not defined PY (py -3.13 -c "import streamlit" >nul 2>nul && set "PY=py -3.13")
if not defined PY set "PY=python"

rem Already running? Just open the browser instead of starting a second copy.
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul && (
    start "" http://localhost:%PORT%
    exit /b 0
)

if /i "%~1"=="hidden" (
    rem Open the browser a few seconds after the server starts, then run the server quietly.
    start "" /b cmd /c "ping -n 5 127.0.0.1 >nul & start http://localhost:%PORT%"
    %PY% -m streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port %PORT% --browser.gatherUsageStats false >> "%TEMP%\pin_label_maker.log" 2>&1
    exit /b
)

%PY% -m streamlit run app.py --server.address 127.0.0.1 --server.port %PORT% --browser.gatherUsageStats false
if errorlevel 1 (
    echo.
    echo Could not start. Install the requirements first: pip install -r requirements.txt
    pause
)
