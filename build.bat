@echo off
REM ============================================================
REM  Build Claude Usage Monitor into a single Windows .exe
REM  Requires: pip install pyinstaller
REM ============================================================

echo Installing / updating dependencies...
pip install -r requirements.txt
pip install pyinstaller

echo.
echo Building executable...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "ClaudeUsageMonitor" ^
    --icon "assets\icon.ico" ^
    --add-data "assets;assets" ^
    --hidden-import "pystray._win32" ^
    --hidden-import "PIL._tkinter_finder" ^
    main.py

echo.
if exist "dist\ClaudeUsageMonitor.exe" (
    echo Build succeeded: dist\ClaudeUsageMonitor.exe
) else (
    echo Build FAILED – check output above.
    exit /b 1
)
