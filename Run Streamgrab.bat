@echo off
setlocal
cd /d "%~dp0"

py -3 "%~dp0server.py" %*
if errorlevel 1 (
    echo.
    echo Streamgrab could not start. Check that Python and the required packages are installed.
    pause
)
