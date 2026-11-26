@echo off
setlocal
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --onefile --windowed --name Streamgrab --icon "%~dp0asset\Streamgrab.ico" --add-data=web/dist:web/dist --collect-all yt_dlp --collect-all webview --collect-all pythonnet --hidden-import webview.platforms.winforms --distpath "%~dp0" --workpath "%~dp0build\pyinstaller" "%~dp0exe_entry.py"
if errorlevel 1 (
    echo.
    echo Streamgrab.exe could not be built. Check that the build requirements are installed.
    pause
)
