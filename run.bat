@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title LocalToolbox - Start Script
cd /d "%~dp0"

echo ========================================
echo   LocalToolbox - Start Script
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.8+ first
    pause
    exit /b 1
)

echo [1/3] Checking dependencies...
python -c "import webview, PIL, cv2" 2>nul
if !errorlevel! neq 0 (
    echo [INFO] Missing dependencies, installing...
    python -m pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo [ERROR] Dependency installation failed
        pause
        exit /b 1
    )
    python -c "import webview, PIL, cv2" 2>nul
    if !errorlevel! neq 0 (
        echo [WARN] pywebview still missing, forcing reinstall...
        python -m pip install --force-reinstall --no-cache-dir "pywebview>=6.2"
    )
)
echo [OK] Core dependencies ready
echo.

echo [2/3] Starting application...
echo   - Closing window keeps it in system tray, right-click tray icon to exit
echo.
python main.py
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Application startup failed, error code: !errorlevel!
    pause
    exit /b 1
)

echo.
echo [3/3] Application exited
pause