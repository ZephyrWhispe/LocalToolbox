@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title LocalToolbox - Build Script
cd /d "%~dp0"

set "EXE=dist\LocalToolbox.exe"

echo ========================================
echo   LocalToolbox - Build Script
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.8+ first
    pause
    exit /b 1
)

echo [1/5] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller || goto :error
echo [OK] Dependencies ready
echo.

echo [2/5] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo [OK] Cleaned
echo.

echo [3/5] Building with PyInstaller...
python -m PyInstaller --noconfirm --clean LocalToolbox.spec || goto :error
echo [OK] Build finished
echo.

echo [4/6] Verifying output...
if not exist "%EXE%" (
    echo [ERROR] Build failed: %EXE% not found
    goto :error
)
for %%F in ("%EXE%") do echo [OK] %%~fF - %%~zF bytes
echo.

echo [5/6] Generating SHA-256 manifest (v5.4 O10)...
python scripts\release_manifest.py dist || goto :error
echo [OK] dist\sha256sums.txt generated (upload together with artifacts)
echo.

echo [6/6] Done! Executable located in dist directory.
pause
exit /b 0

:error
echo.
echo [ERROR] Build failed, please check the messages above.
pause
exit /b 1