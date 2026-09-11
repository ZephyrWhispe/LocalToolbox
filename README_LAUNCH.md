# Application Launch Guide

## Quick Start

### Method 1: Double-click (Recommended for Windows)
- **Start App**: Double-click `run.bat`
- **Build EXE**: Double-click `build.bat`
- **Run Tests**: Double-click `test.bat`

### Method 2: Command Line
```bash
# Start application
python main.py

# Or use PowerShell script
pwsh run.ps1
```

### Method 3: Git Bash / WSL
```bash
python main.py
```

## Available Scripts

| Script | Purpose | Encoding |
|--------|---------|----------|
| `run.bat` | Start application with dependency check | ASCII (English) |
| `build.bat` | Build standalone EXE with PyInstaller | ASCII (English) |
| `test.bat` | Run unit tests and syntax checks | ASCII (English) |
| `run.ps1` | PowerShell alternative for CLI users | UTF-8 |

## Troubleshooting

### Batch file shows garbled text
**Fixed**: All `.bat` files now use English messages (ASCII encoding) to avoid GBK/UTF-8 conflicts.

### "Python not found" error
Install Python 3.8+ from https://www.python.org/downloads/
Ensure "Add Python to PATH" is checked during installation.

### Missing dependencies
Run manually:
```bash
pip install -r requirements.txt
```

### Application won't start
Check if already running in system tray (right-click tray icon to exit first).

## Notes
- The app runs in the background after launch
- Closing the main window minimizes to system tray
- Right-click the tray icon to fully exit the application
