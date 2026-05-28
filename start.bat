@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Video Subtitle Remover - Launching...
echo ============================================

if not exist "videoEnv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found: videoEnv
    echo Please create it first with:
    echo   python -m venv videoEnv
    pause
    exit /b 1
)

call videoEnv\Scripts\activate.bat

echo [INFO] Starting GUI...
python gui.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Application exited with code %errorlevel%
    pause
)