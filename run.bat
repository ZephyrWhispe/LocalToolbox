@echo off
chcp 65001 >nul 2>&1
title LocalToolbox - Start Script

echo ========================================
echo   LocalToolbox - Start Script
echo ========================================
echo.

REM Check Python environment
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found, please install Python 3.8+ first
    pause
    exit /b 1
)

echo [1/3] Checking Python version...
python --version
echo.

echo [2/3] Checking dependencies...
python -c "import pywebview, PIL, cv2" 2>nul
if %errorlevel% neq 0 (
    echo [INFO] Missing dependencies, installing...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Dependency installation failed, please run manually: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
) else (
    echo [OK] Core dependencies OK
)

echo.
echo [3/3] Starting application...
echo.
echo Tips:
echo   - App runs in background
echo   - Closing window keeps it in system tray
echo   - Right-click tray icon to fully exit
echo.

cd /d "%~dp0"
python main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Application startup failed, error code: %errorlevel%
    echo Please check log files or try reinstalling dependencies
    pause
    exit /b 1
)

echo.
echo Application exited
pause
