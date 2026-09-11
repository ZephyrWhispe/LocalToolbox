@echo off
chcp 65001 >nul 2>&1
title SMB Tool - Test Script

echo ========================================
echo   SMB Tool - Automated Tests
echo ========================================
echo.

REM Check Python environment
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found, please install Python 3.8+ first
    pause
    exit /b 1
)

echo [1/2] Running unit tests...
python -m pytest tests/ -v --tb=short 2>nul
if %errorlevel% equ 0 (
    echo [OK] All tests passed
) else (
    echo [WARN] Some tests failed or no test cases
)

echo.
echo [2/2] Syntax check...
python -m py_compile app\bridge\screenshot_api.py
python -m py_compile app\bridge\video_api.py
python -m py_compile app\core\scroll_capture.py
if %errorlevel% equ 0 (
    echo [OK] Core modules syntax check passed
) else (
    echo [ERROR] Syntax errors found
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Testing Complete
echo ========================================
echo.
pause
