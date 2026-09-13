# LocalToolbox - Start Script (PowerShell)
Set-Location $PSScriptRoot
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  LocalToolbox - Start Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python environment
try {
    $pythonVersion = python --version 2>&1
    Write-Host "[1/3] Python version: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python not found, please install Python 3.8+ first" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Check dependencies
Write-Host "[2/3] Checking dependencies..." -ForegroundColor Yellow
try {
    python -c "import webview, PIL, cv2" 2>$null
    Write-Host "[OK] Core dependencies OK" -ForegroundColor Green
} catch {
    Write-Host "[INFO] Missing dependencies, installing..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Dependency installation failed" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "[OK] Dependencies installed" -ForegroundColor Green
}

Write-Host ""
Write-Host "[3/3] Starting application..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Tips:" -ForegroundColor Cyan
Write-Host "  - App runs in background" -ForegroundColor Gray
Write-Host "  - Closing window keeps it in system tray" -ForegroundColor Gray
Write-Host "  - Right-click tray icon to fully exit" -ForegroundColor Gray
Write-Host ""

# Start application

python main.py

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] Application startup failed, error code: $LASTEXITCODE" -ForegroundColor Red
    Write-Host "Please check log files or try reinstalling dependencies" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "Application exited" -ForegroundColor Green
Read-Host "Press Enter to exit"
