@echo off
chcp 65001 >nul 2>&1
title LocalToolbox - Test Script
cd /d "%~dp0"

echo ========================================
echo   LocalToolbox - Automated Tests
echo ========================================
echo.

REM Check Python environment
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found, please install Python 3.8+ first
    pause
    exit /b 1
)

echo [1/3] Contract check (frontend <-> backend)...
python scripts\check_contract.py
if %errorlevel% equ 0 (
    echo [OK] Contract check passed
) else (
    echo [ERROR] Frontend/backend contract mismatch ^(see above^)
    echo         Fix it, or run: python scripts\check_contract.py --warn-only
    pause
    exit /b 1
)

echo.
echo [2/3] Running unit tests...
python -m pytest tests/ -v --tb=short 2>nul
if %errorlevel% equ 0 (
    echo [OK] All tests passed
) else (
    echo [WARN] Some tests failed or no test cases
)

echo.
echo [3/3] Syntax check (all app modules)...
python -m compileall -q app main.py
if %errorlevel% equ 0 (
    echo [OK] All modules syntax check passed
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
echo 提示：UI 层另有两条独立门禁（会短暂弹出真实窗口）：
echo   python scripts\ui_layout_audit.py   布局/生命周期量化审计（含泄漏与材质验证）
echo   python tests\test_ui_smoke.py       真实窗口遍历全部页面的冒烟测试
echo.
pause
